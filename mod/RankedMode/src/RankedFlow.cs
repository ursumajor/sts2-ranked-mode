using System;
using System.Threading.Tasks;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Multiplayer.Game.Lobby;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.Dread;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;

namespace RankedMode;

/// <summary>Opening the ranked screen and embarking (the two steps that talk to the server).</summary>
internal static class RankedFlow
{
    private static bool _opening;

    public static void Open(NSubmenu fromSubmenu)
    {
        if (_opening)
        {
            return;
        }
        TaskHelper.RunSafely(OpenAsync(fromSubmenu));
    }

    private static async Task OpenAsync(NSubmenu fromSubmenu)
    {
        _opening = true;
        try
        {
            // Only reachable with no run save on disk, so an active record here is a run that never got
            // saved (e.g. the game crashed on the first floor). The server already counts it as started.
            if (RankedState.Active != null)
            {
                Log.Warn("[RankedMode] Dropping stale active ranked run with no save file.");
                RankedState.ClearActive();
            }
            await RankedApi.EnsureRegistered();
            await RankedSubmitter.FlushPending(showToast: false);

            var status = await RankedApi.Status();
            long serverTime = status["server_time"]!.GetValue<long>();
            long offset = serverTime - DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var window = status["current_window"];
            var next = status["next_window"];
            if (window == null && next == null)
            {
                RankedUi.Popup("Ranked is closed", "No ranked window is scheduled right now.");
                return;
            }
            if (window != null && WhyCantPlay(window) is { } problem)
            {
                RankedUi.Popup(problem.Title, problem.Body);
                return;
            }
            if (!GodotObject.IsInstanceValid(fromSubmenu) || !fromSubmenu.IsInsideTree())
            {
                return;
            }
            var stack = (NSubmenuStack?)AccessTools.Field(typeof(NSubmenu), "_stack").GetValue(fromSubmenu);
            if (stack == null)
            {
                return;
            }
            if (window != null)
            {
                RankedSession.BeginScreen(
                    window["id"]!.GetValue<string>(),
                    window["character"]!.GetValue<string>(),
                    window["ends_at"]!.GetValue<long>(),
                    offset);
            }
            else
            {
                // Closed: show the countdown to the next window on the same screen.
                RankedSession.BeginClosedScreen(next!["character"]!.GetValue<string>(), next["starts_at"]!.GetValue<long>(), offset);
            }
            var screen = stack.GetSubmenuType<NDreadScreen>();
            screen.InitializeSingleplayer();
            stack.Push(screen);
        }
        catch (Exception e)
        {
            RankedSession.EndScreen();
            Log.Error($"[RankedMode] Could not open ranked: {e}");
            RankedUi.Popup("Ranked unavailable", RankedApi.Describe(e));
        }
        finally
        {
            _opening = false;
        }
    }

    private static (string Title, string Body)? WhyCantPlay(System.Text.Json.Nodes.JsonNode window)
    {
        if (window["your_run"] != null)
        {
            return ("Already played", $"You've used your ranked attempt for this window. Leaderboard: {RankedConfig.ServerUrl}/");
        }
        string? requiredVersion = window["game_version"]?.GetValue<string>();
        if (!string.IsNullOrEmpty(requiredVersion) && requiredVersion != RankedMod.GameVersion)
        {
            return ("Wrong game version", $"This ranked window requires {requiredVersion}, but you're on {RankedMod.GameVersion}.");
        }
        return null;
    }

    /// <summary>
    /// The closed-state countdown reached zero: ask the server whether the window is open now, and if so switch
    /// the screen to the open state in place.
    /// </summary>
    public static async Task RefreshClosedScreenAsync(NDreadScreen screen)
    {
        RankedSession.Refreshing = true;
        try
        {
            var status = await RankedApi.Status();
            if (!GodotObject.IsInstanceValid(screen) || !RankedSession.ScreenActive || !RankedSession.Closed)
            {
                return;
            }
            long serverTime = status["server_time"]!.GetValue<long>();
            long offset = serverTime - DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var window = status["current_window"];
            if (window == null)
            {
                // Not open yet (clocks a few seconds apart, or the schedule changed): follow the next window.
                var next = status["next_window"];
                if (next == null)
                {
                    RankedSession.Notice = "No ranked window is scheduled right now.";
                }
                else
                {
                    RankedSession.BeginClosedScreen(next["character"]!.GetValue<string>(), next["starts_at"]!.GetValue<long>(), offset);
                    Patches.DreadScreenPatches.ApplyRankedUi(screen);
                }
                RankedSession.RetryAt = RankedSession.ServerNow + 5;
                return;
            }
            if (WhyCantPlay(window) is { } problem)
            {
                RankedSession.Notice = problem.Body;
                RankedSession.RetryAt = long.MaxValue;
                return;
            }
            RankedSession.BeginScreen(window["id"]!.GetValue<string>(), window["character"]!.GetValue<string>(),
                window["ends_at"]!.GetValue<long>(), offset);
            Patches.DreadScreenPatches.ApplyRankedUi(screen);
        }
        catch (Exception e)
        {
            Log.Warn($"[RankedMode] Ranked status check failed, retrying: {RankedApi.Describe(e)}");
            RankedSession.RetryAt = RankedSession.ServerNow + 10;
        }
        finally
        {
            RankedSession.Refreshing = false;
        }
    }

    /// <summary>
    /// Called instead of the Dread screen's Embark handler: fetch the secret seed (this uses up the
    /// attempt), put it in the lobby, then run the original handler.
    /// </summary>
    public static async Task EmbarkAsync(NDreadScreen screen)
    {
        RankedSession.EmbarkInFlight = true;
        var embark = Traverse.Create(screen).Field("_embarkButton").GetValue<NButton>();
        var back = Traverse.Create(screen).Field("_backButton").GetValue<NButton>();
        embark?.Disable();
        back?.Disable();
        try
        {
            if (RankedSession.ServerNow >= RankedSession.WindowEndsAt)
            {
                throw new RankedApiException(403, "This ranked window has closed.");
            }
            var res = await RankedApi.StartRun(RankedSession.WindowId!);
            string seed = res["seed"]!.GetValue<string>();
            string runId = res["run_id"]!.GetValue<string>();
            RankedState.BeginRun(runId, seed, RankedSession.WindowId!);
            RankedSession.SecretSeed = seed;

            if (!GodotObject.IsInstanceValid(screen) || !RankedSession.ScreenActive)
            {
                return;
            }
            // StartRunLobby.SetSeed would notify the Dread screen, which throws on seed changes ("Seed should not
            // be changed in Dread mode!"). Set the property directly; BeginRun reads it when the player readies up.
            AccessTools.Property(typeof(StartRunLobby), nameof(StartRunLobby.Seed)).SetValue(screen.Lobby, seed);
            if (NGame.Instance != null)
            {
                NGame.Instance.DebugSeedOverride = null;
            }
            RankedSession.BypassEmbarkPatch = true;
            try
            {
                AccessTools.Method(typeof(NDreadScreen), "OnEmbarkPressed").Invoke(screen, new object?[] { embark });
            }
            finally
            {
                RankedSession.BypassEmbarkPatch = false;
            }
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Ranked embark failed: {e}");
            if (GodotObject.IsInstanceValid(screen))
            {
                embark?.Enable();
                back?.Enable();
            }
            RankedUi.Popup("Couldn't start ranked run", RankedApi.Describe(e));
        }
        finally
        {
            RankedSession.EmbarkInFlight = false;
        }
    }
}
