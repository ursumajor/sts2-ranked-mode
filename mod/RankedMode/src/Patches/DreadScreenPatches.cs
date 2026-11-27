using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.Dread;

namespace RankedMode.Patches;

/// <summary>
/// Reuses the Dread screen for ranked runs. These patches only do anything while
/// <see cref="RankedSession.ScreenActive" /> is set, so normal Dread is untouched.
///
/// In ranked:
/// - the character-choice UI (character row, rotation option, streak chain, Dread records) is hidden and
///   selection is locked to the window's character;
/// - while no window is open, the screen shows a large countdown to the next one (Embark hidden, Back works)
///   and switches to the open state by itself when it opens;
/// - while open, a small countdown shows when the window closes;
/// - the rotating-character option never begins a rotation;
/// - the player's Dread preferences and rotation progress aren't touched;
/// - Embark first fetches the secret seed from the server;
/// </summary>
[HarmonyPatch(typeof(NDreadScreen))]
internal static class DreadScreenPatches
{
    private static int _lastShownSecond = -1;

    private static T? F<T>(NDreadScreen screen, string field) where T : class =>
        Traverse.Create(screen).Field(field).GetValue<T>();

    private static List<NCharacterSelectButton> Buttons(NDreadScreen screen) =>
        F<List<NCharacterSelectButton>>(screen, "_charSelectButtons") ?? new List<NCharacterSelectButton>();

    private static NCharacterSelectButton? AssignedButton(NDreadScreen screen)
    {
        var rotating = F<NCharacterSelectButton>(screen, "_rotatingCharacterButton");
        return Buttons(screen).FirstOrDefault(b => b != rotating && b.Character?.Id.ToString() == RankedSession.Character);
    }

    private const string CountdownName = "RankedCountdown";   // small, top centre, while open
    private const string ClosedPanelName = "RankedClosedPanel"; // large, centred, while closed

    // Nodes hidden for ranked, with their visibility before we touched them, so normal Dread gets them back.
    private static readonly Dictionary<Control, bool> _hidden = new();

    // Character-choice UI that ranked doesn't use: the character row, the rotating-character option, the
    // Dread win-streak chain and the per-character Dread records.
    private static readonly string[] CharacterUiFields =
    {
        "_charButtonContainer", "_startRotationTickbox", "_startRotationLabel", "_rotatingImage",
        "_chainVisualization", "_scoreLabels", "_scoreValues",
    };

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NDreadScreen.OnSubmenuOpened))]
    private static void AfterOpened(NDreadScreen __instance) => ApplyRankedUi(__instance);

    /// <summary>Sets up the screen for the current state: normal Dread, ranked open, or ranked closed.</summary>
    public static void ApplyRankedUi(NDreadScreen screen)
    {
        var countdown = Label(screen, CountdownName, 20, Control.LayoutPreset.CenterTop, 18);
        var closedPanel = Label(screen, ClosedPanelName, 40, Control.LayoutPreset.Center, 0);
        _lastShownSecond = -1;
        if (!RankedSession.ScreenActive)
        {
            countdown.Visible = closedPanel.Visible = false;
            RestoreHidden();
            return;
        }

        HideCharacterUi(screen);
        var embark = F<NButton>(screen, "_embarkButton");
        if (RankedSession.Closed)
        {
            Hide(embark);
            embark?.Disable();
        }
        else if (embark != null && _hidden.Remove(embark, out bool wasVisible))
        {
            embark.Visible = wasVisible;
        }
        countdown.Visible = !RankedSession.Closed;
        closedPanel.Visible = RankedSession.Closed;

        // Select the window's character (the background art and name show it, even while closed).
        var assigned = AssignedButton(screen);
        if (assigned == null)
        {
            Log.Error($"[RankedMode] Ranked character {RankedSession.Character} not found on the Dread screen.");
            RankedUi.Popup("Ranked", $"This ranked window uses {RankedSession.Character}, which this game version doesn't have.");
            embark?.Disable();
            return;
        }
        Traverse.Create(screen).Field("_overriddenFocusedControl").SetValue(assigned);
        Callable.From(() =>
        {
            if (GodotObject.IsInstanceValid(assigned) && RankedSession.ScreenActive)
            {
                if (assigned.IsSelected)
                {
                    screen.SelectCharacter(assigned);   // re-run the selection logic (e.g. closed -> open)
                }
                else
                {
                    assigned.Select();
                }
            }
        }).CallDeferred();
        UpdateLabels(screen);
    }

    private static Label Label(NDreadScreen screen, string name, int size, Control.LayoutPreset preset, int offsetTop)
    {
        if (screen.GetNodeOrNull<Label>(name) is { } existing)
        {
            return existing;
        }
        var label = new Label { Name = name, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        label.AddThemeFontSizeOverride("font_size", size);
        label.AddThemeColorOverride("font_color", new Color(0.95f, 0.82f, 0.45f));
        label.AddThemeColorOverride("font_outline_color", Colors.Black);
        label.AddThemeConstantOverride("outline_size", size / 4);
        label.AddThemeConstantOverride("line_spacing", size / 5);
        label.MouseFilter = Control.MouseFilterEnum.Ignore;
        screen.AddChild(label);
        label.SetAnchorsPreset(preset);
        label.GrowHorizontal = Control.GrowDirection.Both;
        label.GrowVertical = preset == Control.LayoutPreset.Center ? Control.GrowDirection.Both : Control.GrowDirection.End;
        label.OffsetTop += offsetTop;
        label.Visible = false;
        return label;
    }

    private static void Hide(Control? node)
    {
        if (node == null)
        {
            return;
        }
        _hidden.TryAdd(node, node.Visible);
        node.Visible = false;
    }

    private static void HideCharacterUi(NDreadScreen screen)
    {
        foreach (string field in CharacterUiFields)
        {
            Hide(F<Control>(screen, field));
        }
    }

    private static void RestoreHidden()
    {
        foreach (var (node, visible) in _hidden)
        {
            if (GodotObject.IsInstanceValid(node))
            {
                node.Visible = visible;
            }
        }
        _hidden.Clear();
    }

    private static string Countdown(long seconds)
    {
        var t = TimeSpan.FromSeconds(Math.Max(0, seconds));
        return t.TotalDays >= 1 ? $"{(int)t.TotalDays}d {t:hh\\:mm\\:ss}" : t.ToString("hh\\:mm\\:ss");
    }

    private static string CharacterName() =>
        (RankedSession.Character?.Split('.').Last() ?? "?").Replace('_', ' ') is var n && n.Length > 0
            ? char.ToUpperInvariant(n[0]) + n[1..].ToLowerInvariant() : "?";

    private static void UpdateLabels(NDreadScreen screen)
    {
        long now = RankedSession.ServerNow;
        if (RankedSession.Closed)
        {
            if (screen.GetNodeOrNull<Label>(ClosedPanelName) is not { } panel)
            {
                return;
            }
            if (RankedSession.Notice != null)
            {
                panel.Text = $"RANKED\n{RankedSession.Notice}";
                return;
            }
            long left = RankedSession.OpensAt - now;
            var local = DateTimeOffset.FromUnixTimeSeconds(RankedSession.OpensAt).ToLocalTime();
            panel.Text = left > 0
                ? $"RANKED\nopens in {Countdown(left)}\n\nNext run: {CharacterName()} · {local:ddd HH:mm}"
                : "RANKED\nopening…";
        }
        else if (screen.GetNodeOrNull<Label>(CountdownName) is { } countdown)
        {
            long left = RankedSession.WindowEndsAt - now;
            countdown.Text = left > 0 ? $"Ranked · {CharacterName()} · closes in {Countdown(left)}" : "Ranked · window closed";
        }
    }

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NDreadScreen._Process))]
    private static void AfterProcess(NDreadScreen __instance)
    {
        if (!RankedSession.ScreenActive)
        {
            return;
        }
        long now = RankedSession.ServerNow;
        if (RankedSession.Closed && now >= RankedSession.OpensAt && RankedSession.Notice == null
            && !RankedSession.Refreshing && now >= RankedSession.RetryAt)
        {
            RankedSession.Refreshing = true;
            TaskHelper.RunSafely(RankedFlow.RefreshClosedScreenAsync(__instance));
        }
        int second = (int)(now % 60);
        if (second == _lastShownSecond)
        {
            return;
        }
        _lastShownSecond = second;
        UpdateLabels(__instance);
    }

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NDreadScreen.OnSubmenuClosed))]
    private static void AfterClosed(NDreadScreen __instance)
    {
        foreach (string name in new[] { CountdownName, ClosedPanelName })
        {
            if (__instance.GetNodeOrNull<Label>(name) is { } label)
            {
                label.Visible = false;
            }
        }
        RestoreHidden();
        // Buttons we disabled are re-enabled by the base game's OnSubmenuOpened next time.
        RankedSession.EndScreen();
    }

    /// <summary>Any attempt to select another character (click, controller, preferred-character focus) is redirected.</summary>
    [HarmonyPrefix]
    [HarmonyPatch(nameof(NDreadScreen.SelectCharacter))]
    private static bool BeforeSelect(NDreadScreen __instance, NCharacterSelectButton charSelectButton)
    {
        if (!RankedSession.ScreenActive)
        {
            return true;
        }
        if (charSelectButton.Character?.Id.ToString() == RankedSession.Character
            && charSelectButton != F<NCharacterSelectButton>(__instance, "_rotatingCharacterButton"))
        {
            return true;
        }
        charSelectButton.Deselect();
        var assigned = AssignedButton(__instance);
        if (assigned != null && !assigned.IsSelected)
        {
            assigned.Select();
        }
        return false;
    }

    /// <summary>
    /// Ranked plays the window's character even if this player hasn't unlocked it yet, so re-enable Embark
    /// (the base game disables it for locked characters). Also keep the rotation tickbox hidden.
    /// </summary>
    [HarmonyPostfix]
    [HarmonyPatch(nameof(NDreadScreen.SelectCharacter))]
    private static void AfterSelect(NDreadScreen __instance)
    {
        if (!RankedSession.ScreenActive)
        {
            return;
        }
        HideCharacterUi(__instance);
        bool animating = Traverse.Create(__instance).Field("_doingChainAnimation").GetValue<bool>();
        if (RankedSession.Closed)
        {
            F<NButton>(__instance, "_embarkButton")?.Disable();
        }
        else if (!animating && !RankedSession.EmbarkInFlight)
        {
            F<NButton>(__instance, "_embarkButton")?.Enable();
        }
    }

    [HarmonyPostfix]
    [HarmonyPatch("SetSelectedCharacterVisuals")]
    private static void AfterVisuals(NDreadScreen __instance)
    {
        if (RankedSession.ScreenActive)
        {
            // The base game re-shows the rotation tickbox, streak chain and rotating image on every selection.
            foreach (string field in CharacterUiFields)
            {
                if (F<Control>(__instance, field) is { } node)
                {
                    Hide(node);
                }
            }
        }
    }

    [HarmonyPrefix]
    [HarmonyPatch("IsBeginningRotation")]
    private static bool NeverRotate(ref bool __result)
    {
        if (!RankedSession.ScreenActive)
        {
            return true;
        }
        __result = false;
        return false;
    }

    /// <summary>Don't overwrite the player's preferred Dread character / rotation settings with ranked's.</summary>
    [HarmonyPrefix]
    [HarmonyPatch("UpdateProgressForRotation")]
    private static bool SkipProgressForRotation() => !RankedSession.ScreenActive;

    [HarmonyPrefix]
    [HarmonyPatch("OnEmbarkPressed")]
    private static bool OnEmbarkPressed(NDreadScreen __instance)
    {
        if (!RankedSession.ScreenActive || RankedSession.BypassEmbarkPatch)
        {
            return true;
        }
        if (RankedSession.Closed)
        {
            return false;   // nothing to embark on yet (also blocks the Embark hotkey)
        }
        if (!RankedSession.EmbarkInFlight)
        {
            TaskHelper.RunSafely(RankedFlow.EmbarkAsync(__instance));
        }
        return false;
    }

    /// <summary>Last check before the run starts: only the server seed and the window's character.</summary>
    [HarmonyPrefix]
    [HarmonyPatch(nameof(NDreadScreen.BeginRun))]
    private static bool BeforeBeginRun(NDreadScreen __instance, string seed)
    {
        if (!RankedSession.ScreenActive)
        {
            return true;
        }
        string? character = __instance.Lobby.LocalPlayer.character?.Id.ToString();
        if (RankedSession.SecretSeed == null || seed != RankedSession.SecretSeed || character != RankedSession.Character)
        {
            Log.Error($"[RankedMode] Refusing to begin ranked run (seed ok: {seed == RankedSession.SecretSeed}, character: {character}).");
            RankedUi.Popup("Ranked", "Something went wrong starting the ranked run. Please go back and try again.");
            return false;
        }
        return true;
    }
}
