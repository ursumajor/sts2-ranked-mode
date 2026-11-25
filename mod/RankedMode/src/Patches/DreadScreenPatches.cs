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
/// - selection is locked to the window's character (all other buttons disabled, any select redirected);
/// - the rotating-character option is hidden and never begins a rotation;
/// - the player's Dread preferences and rotation progress aren't touched;
/// - Embark first fetches the secret seed from the server;
/// - a banner shows the window and its countdown.
/// </summary>
[HarmonyPatch(typeof(NDreadScreen))]
internal static class DreadScreenPatches
{
    private const string BannerName = "RankedBanner";
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

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NDreadScreen.OnSubmenuOpened))]
    private static void AfterOpened(NDreadScreen __instance)
    {
        var banner = __instance.GetNodeOrNull<Label>(BannerName);
        if (!RankedSession.ScreenActive)
        {
            if (banner != null)
            {
                banner.Visible = false;
            }
            return;
        }

        var assigned = AssignedButton(__instance);
        if (assigned == null)
        {
            Log.Error($"[RankedMode] Ranked character {RankedSession.Character} not found on the Dread screen.");
            RankedUi.Popup("Ranked", $"This ranked window uses {RankedSession.Character}, which this game version doesn't have.");
            F<NButton>(__instance, "_embarkButton")?.Disable();
            return;
        }
        foreach (var button in Buttons(__instance))
        {
            if (button != assigned)
            {
                button.Disable();
            }
        }
        var rotating = F<NCharacterSelectButton>(__instance, "_rotatingCharacterButton");
        if (rotating != null)
        {
            rotating.Visible = false;
        }
        Traverse.Create(__instance).Field("_overriddenFocusedControl").SetValue(assigned);
        Callable.From(() =>
        {
            if (GodotObject.IsInstanceValid(assigned) && RankedSession.ScreenActive)
            {
                assigned.Select();
            }
        }).CallDeferred();

        if (banner == null)
        {
            banner = new Label { Name = BannerName, HorizontalAlignment = HorizontalAlignment.Center };
            banner.AddThemeFontSizeOverride("font_size", 30);
            banner.AddThemeColorOverride("font_color", new Color(0.95f, 0.82f, 0.45f));
            banner.AddThemeColorOverride("font_outline_color", Colors.Black);
            banner.AddThemeConstantOverride("outline_size", 8);
            banner.MouseFilter = Control.MouseFilterEnum.Ignore;
            __instance.AddChild(banner);
            banner.SetAnchorsPreset(Control.LayoutPreset.CenterTop);
            banner.GrowHorizontal = Control.GrowDirection.Both;
            banner.OffsetTop = 24;
        }
        banner.Visible = true;
        _lastShownSecond = -1;
        UpdateBanner(banner);
    }

    private static void UpdateBanner(Label banner)
    {
        long left = RankedSession.WindowEndsAt - RankedSession.ServerNow;
        string name = RankedSession.Character?.Split('.').Last() ?? "?";
        banner.Text = left > 0
            ? $"{RankedLoc.Title} · {RankedSession.WindowId} · {name} · closes in {TimeSpan.FromSeconds(left):hh\\:mm\\:ss}"
            : $"{RankedLoc.Title} · {RankedSession.WindowId} · window closed";
    }

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NDreadScreen._Process))]
    private static void AfterProcess(NDreadScreen __instance)
    {
        if (!RankedSession.ScreenActive)
        {
            return;
        }
        int second = (int)(RankedSession.ServerNow % 60);
        if (second == _lastShownSecond)
        {
            return;
        }
        _lastShownSecond = second;
        if (__instance.GetNodeOrNull<Label>(BannerName) is { } banner)
        {
            UpdateBanner(banner);
        }
    }

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NDreadScreen.OnSubmenuClosed))]
    private static void AfterClosed(NDreadScreen __instance)
    {
        if (__instance.GetNodeOrNull<Label>(BannerName) is { } banner)
        {
            banner.Visible = false;
        }
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
        HideRotationTickbox(__instance);
        bool animating = Traverse.Create(__instance).Field("_doingChainAnimation").GetValue<bool>();
        if (!animating && !RankedSession.EmbarkInFlight)
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
            HideRotationTickbox(__instance);
        }
    }

    private static void HideRotationTickbox(NDreadScreen screen)
    {
        if (F<Control>(screen, "_startRotationTickbox") is { } tickbox)
        {
            tickbox.Visible = false;
        }
        if (F<Control>(screen, "_startRotationLabel") is { } label)
        {
            label.Visible = false;
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
