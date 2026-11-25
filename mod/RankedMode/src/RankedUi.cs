using System;
using System.Collections.Generic;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Vfx;

namespace RankedMode;

internal static class RankedUi
{
    public static void Popup(string title, string body)
    {
        try
        {
            var popup = NErrorPopup.Create(title, body, showReportBugButton: false);
            if (popup != null && NModalContainer.Instance != null)
            {
                NModalContainer.Instance.Add(popup);
                return;
            }
        }
        catch (Exception e)
        {
            Log.Warn($"[RankedMode] Could not show popup: {e.Message}");
        }
        Toast(title + ": " + body);
    }

    public static void Toast(string text)
    {
        try
        {
            var vfx = NFullscreenTextVfx.Create(text);
            if (vfx != null)
            {
                NGame.Instance?.AddChildSafely(vfx);
            }
        }
        catch (Exception e)
        {
            Log.Warn($"[RankedMode] Could not show toast: {e.Message}");
        }
    }

    public static string FormatLocal(long unixSeconds, long serverNow)
    {
        var local = DateTimeOffset.FromUnixTimeSeconds(unixSeconds).ToLocalTime();
        var until = TimeSpan.FromSeconds(Math.Max(0, unixSeconds - serverNow));
        string rel = until.TotalHours >= 1 ? $"{(int)until.TotalHours}h {until.Minutes}m" : $"{until.Minutes}m {until.Seconds}s";
        return $"{local:ddd HH:mm} (in {rel})";
    }
}

/// <summary>Adds the mod's strings to the game's main_menu_ui loc table (no PCK needed).</summary>
internal static class RankedLoc
{
    public const string Title = "RANKED";

    private static readonly Dictionary<string, string> MainMenu = new()
    {
        ["RANKED.title"] = "Ranked",
        ["RANKED.description"] = "A Dread run on a hidden seed, open only during ranked windows. Everyone plays the same character and seed. One attempt, no reloads.",
        ["RANKED.LOCKED.description"] = "Ranked is unavailable.",
    };

    public static void Inject(LocManager? manager = null)
    {
        try
        {
            // The first language load happens inside LocManager's constructor, before LocManager.Instance is
            // assigned, so callers pass the instance explicitly when they have it.
            (manager ?? LocManager.Instance)?.GetTable("main_menu_ui").MergeWith(MainMenu);
        }
        catch (Exception)
        {
            // Tables not loaded yet; the SetLanguageInternal postfix will inject later.
        }
    }
}
