using HarmonyLib;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Screens.RunHistoryScreen;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.addons.mega_text;

namespace RankedMode.Patches;

/// <summary>The in-run seed label (bottom corner, toggled with the version-info hotkey).</summary>
[HarmonyPatch(typeof(NRun), nameof(NRun._Ready))]
internal static class HideRunSeedLabelPatch
{
    private static void Postfix(NRun __instance)
    {
        var label = Traverse.Create(__instance).Field("_seedLabel").GetValue<MegaLabel>();
        if (label != null && RankedState.IsRankedSeed(label.Text))
        {
            label.SetTextAutoSize("RANKED");
        }
    }
}

/// <summary>Run history screen: ranked seeds stay hidden so they can't be shared while others still play.</summary>
[HarmonyPatch(typeof(NRunHistory), "LoadTimeDetails")]
internal static class HideRunHistorySeedPatch
{
    private static void Postfix(NRunHistory __instance, RunHistory history)
    {
        if (!RankedState.IsRankedSeed(history.Seed))
        {
            return;
        }
        var t = Traverse.Create(__instance);
        var loc = t.Field("_seedLocString").GetValue<LocString>();
        var label = t.Field("_seedLabel").GetValue<MegaRichTextLabel>();
        if (loc != null && label != null)
        {
            loc.Add("Seed", "HIDDEN (ranked)");
            label.Text = loc.GetFormattedText();
        }
    }
}

/// <summary>The game logs the seed when a run starts ("Embarking on a DAILY ... Seed: X"). Redact it.</summary>
[HarmonyPatch(typeof(Logger), nameof(Logger.LogMessage), typeof(LogLevel), typeof(LogType), typeof(string), typeof(int))]
internal static class RedactSeedInLogsPatch
{
    private static void Prefix(ref string text)
    {
        if (text == null)
        {
            return;
        }
        string? secret = RankedSession.SecretSeed;
        if (secret != null && text.Contains(secret))
        {
            text = text.Replace(secret, "<ranked-seed>");
        }
        string? active = RankedState.Active?.Seed;
        if (!string.IsNullOrEmpty(active) && text.Contains(active))
        {
            text = text.Replace(active, "<ranked-seed>");
        }
    }
}

/// <summary>Re-inject our strings whenever the language (and so the loc tables) is reloaded.</summary>
[HarmonyPatch(typeof(LocManager), "SetLanguageInternal")]
internal static class LocInjectPatch
{
    private static void Postfix(LocManager __instance) => RankedLoc.Inject(__instance);
}
