using System;
using System.Threading.Tasks;
using HarmonyLib;
using MegaCrit.Sts2.Core.Daily;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;

namespace RankedMode.Patches;

/// <summary>Run end → submit. Ranked runs never reach the official Steam daily leaderboard.</summary>
[HarmonyPatch(typeof(RunManager), nameof(RunManager.OnEnded))]
internal static class RunEndedPatch
{
    private static RunState? GetState(RunManager manager) =>
        (RunState?)AccessTools.Property(typeof(RunManager), "State").GetValue(manager);

    private static void Prefix(RunManager __instance, out bool __state)
    {
        __state = false;
        if (__instance.HasRunEnded)
        {
            return;
        }
        string? seed = GetState(__instance)?.Rng?.StringSeed;
        if (RankedState.IsActiveSeed(seed))
        {
            __state = true;
            RankedSession.SuppressNextDailyUpload = true;
        }
    }

    private static void Postfix(RunManager __instance, bool isVictory, bool __state)
    {
        if (!__state)
        {
            return;
        }
        try
        {
            SerializableRun run = __instance.ToSave(null);
            RankedSubmitter.Submit(run, isVictory, __instance.IsAbandoned);
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Failed to submit ranked run at run end: {e}");
        }
        finally
        {
            RankedSession.SuppressNextDailyUpload = false;
        }
    }
}

[HarmonyPatch(typeof(DailyRunUtility), nameof(DailyRunUtility.UploadScore))]
internal static class DailyUploadPatch
{
    private static bool Prefix(ref Task __result)
    {
        if (!RankedSession.SuppressNextDailyUpload)
        {
            return true;
        }
        RankedSession.SuppressNextDailyUpload = false;
        Log.Info("[RankedMode] Skipping Steam daily leaderboard upload for a ranked run.");
        __result = Task.CompletedTask;
        return false;
    }
}

/// <summary>No resuming ranked runs: Continue is disabled, and abandoning submits the run as abandoned.</summary>
[HarmonyPatch(typeof(NMainMenu))]
internal static class MainMenuPatches
{
    private static SerializableRun? SavedRun(NMainMenu menu) =>
        Traverse.Create(menu).Field("_readRunSaveResult").GetValue<ReadSaveResult<SerializableRun>>()?.SaveData;

    private static bool IsRanked(SerializableRun? run) => RankedState.IsRankedSeed(run?.SerializableRng?.Seed);

    /// <summary>Unlock everything on the modded profile before the main menu builds its buttons.</summary>
    [HarmonyPrefix]
    [HarmonyPatch(nameof(NMainMenu._Ready))]
    private static void BeforeMainMenuReady() => RankedUnlocker.EnsureUnlocked();

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NMainMenu.RefreshButtons))]
    private static void AfterRefreshButtons(NMainMenu __instance)
    {
        if (IsRanked(SavedRun(__instance)))
        {
            Traverse.Create(__instance).Field("_continueButton").GetValue<NMainMenuTextButton>()?.SetEnabled(false);
        }
    }

    [HarmonyPrefix]
    [HarmonyPatch("OnContinueButtonPressed")]
    private static bool BeforeContinue(NMainMenu __instance)
    {
        if (!IsRanked(SavedRun(__instance)))
        {
            return true;
        }
        RankedUi.Popup("Ranked run",
            "Ranked runs can't be resumed from a save. Abandon the run to submit your result so far.");
        return false;
    }

    /// <summary>
    /// Abandoning from the main menu: the game writes the run history file inside AbandonRun, so remember the
    /// save here and submit afterwards, once the .run file has been captured.
    /// </summary>
    [HarmonyPrefix]
    [HarmonyPatch(nameof(NMainMenu.AbandonRun))]
    private static void BeforeAbandon(NMainMenu __instance, out SerializableRun? __state)
    {
        __state = null;
        var run = SavedRun(__instance);
        if (!IsRanked(run))
        {
            return;
        }
        __state = run;
        RankedSession.SuppressNextDailyUpload = true;
    }

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NMainMenu.AbandonRun))]
    private static void AfterAbandon(SerializableRun? __state)
    {
        RankedSession.SuppressNextDailyUpload = false;
        if (__state == null)
        {
            return;
        }
        try
        {
            RankedSubmitter.Submit(__state, victory: false, abandoned: true);
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Failed to submit abandoned ranked run: {e}");
        }
    }
}

/// <summary>
/// Capture the exact text of the game's .run history file for the active ranked run (the same
/// JsonSerializationUtility.ToJson call the game uses to write history/&lt;start_time&gt;.run).
/// Every run end path goes through here: death, victory, in-run abandon, main-menu abandon.
/// </summary>
[HarmonyPatch(typeof(SaveManager), nameof(SaveManager.SaveRunHistory))]
internal static class RunFileCapturePatch
{
    private static void Postfix(RunHistory history)
    {
        try
        {
            if (RankedState.IsActiveSeed(history?.Seed))
            {
                RankedSubmitter.CaptureRunFile(history!.Seed, JsonSerializationUtility.ToJson(history));
            }
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Could not capture the .run file: {e.Message}");
        }
    }
}

/// <summary>Defense in depth: refuse to load a ranked save through any code path.</summary>
[HarmonyPatch(typeof(RunManager), nameof(RunManager.SetUpSavedSingleplayer))]
internal static class BlockRankedLoadPatch
{
    private static void Prefix(SerializableRun save)
    {
        if (RankedState.IsRankedSeed(save?.SerializableRng?.Seed))
        {
            throw new InvalidOperationException("Ranked runs cannot be continued from a save.");
        }
    }
}
