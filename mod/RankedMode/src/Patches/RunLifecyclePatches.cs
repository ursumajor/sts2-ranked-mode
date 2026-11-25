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

    [HarmonyPrefix]
    [HarmonyPatch(nameof(NMainMenu.AbandonRun))]
    private static void BeforeAbandon(NMainMenu __instance)
    {
        var run = SavedRun(__instance);
        if (!IsRanked(run))
        {
            return;
        }
        RankedSession.SuppressNextDailyUpload = true;
        try
        {
            RankedSubmitter.Submit(run!, victory: false, abandoned: true);
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Failed to submit abandoned ranked run: {e}");
        }
    }

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NMainMenu.AbandonRun))]
    private static void AfterAbandon()
    {
        RankedSession.SuppressNextDailyUpload = false;
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
