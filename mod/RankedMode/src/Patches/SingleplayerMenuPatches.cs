using System;
using System.Linq;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;

namespace RankedMode.Patches;

/// <summary>Adds a "Ranked" button next to Daily in the singleplayer submenu. It opens the Dread screen in ranked mode.</summary>
[HarmonyPatch(typeof(NSingleplayerSubmenu))]
internal static class SingleplayerMenuPatches
{
    private const string ButtonName = "RankedButton";

    // Golden yellow on the submenu panel texture (Daily uses h 0.48, Dread 0.75, Custom 0.93).
    private const float GoldHue = 0.955f;
    private const float GoldSaturation = 2.4f;
    private const float GoldBrightness = 0.8f;

    /// <summary>
    /// NSubmenuButton.OnEnable resets the panel's saturation to 1, which would wash the gold out to khaki.
    /// Put it back for the Ranked card only.
    /// </summary>
    [HarmonyPostfix]
    [HarmonyPatch(typeof(NSubmenuButton), "OnEnable")]
    private static void KeepGoldSaturation(NSubmenuButton __instance)
    {
        if (__instance.Name == ButtonName && __instance.GetNodeOrNull<Control>("BgPanel")?.Material is ShaderMaterial m)
        {
            m.SetShaderParameter("s", GoldSaturation);
        }
    }

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NSingleplayerSubmenu._Ready))]
    private static void AfterReady(NSingleplayerSubmenu __instance)
    {
        try
        {
            var daily = Traverse.Create(__instance).Field("_dailyButton").GetValue<NSubmenuButton>();
            var parent = daily.GetParent();
            if (parent.GetNodeOrNull(ButtonName) != null)
            {
                return;
            }
            // Leave the Daily button's signal connections behind: only copy groups, script and scene structure.
            var flags = (int)(Node.DuplicateFlags.Groups | Node.DuplicateFlags.Scripts | Node.DuplicateFlags.UseInstantiation);
            var ranked = (NSubmenuButton)daily.Duplicate(flags);
            ranked.Name = ButtonName;
            // The hover shader material is a shared resource. Give the copy its own so hovering one button
            // doesn't light up the other.
            var bg = ranked.GetNodeOrNull<Control>("BgPanel");
            if (bg?.Material != null)
            {
                var material = (ShaderMaterial)bg.Material.Duplicate();
                // Golden yellow. The panel uses hsv.gdshader: h rotates the hue (YIQ), s scales saturation,
                // v scales brightness. Values were chosen by running the shader maths on submenu_panel.png.
                // v is read as the card's resting brightness when it's added to the tree, so set it first.
                material.SetShaderParameter("h", GoldHue);
                material.SetShaderParameter("s", GoldSaturation);
                material.SetShaderParameter("v", GoldBrightness);
                bg.Material = material;
            }
            parent.AddChild(ranked);
            parent.MoveChild(ranked, daily.GetIndex() + 1);
            ranked.Connect(NClickableControl.SignalName.Released, Callable.From<NButton>(_ => RankedFlow.Open(__instance)));
            RankedLoc.Inject();
            ranked.SetLocalization("RANKED");
            ranked.SetIcon("dread");
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Could not add Ranked button: {e}");
        }
    }

    [HarmonyPostfix]
    [HarmonyPatch(nameof(NSingleplayerSubmenu.OnSubmenuOpened))]
    private static void AfterOpened(NSingleplayerSubmenu __instance)
    {
        try
        {
            var daily = Traverse.Create(__instance).Field("_dailyButton").GetValue<NSubmenuButton>();
            var parent = daily.GetParent();
            if (parent.GetNodeOrNull<NSubmenuButton>(ButtonName) is not { } ranked)
            {
                return;
            }
            ranked.SetEnabled(true);
            RankedLoc.Inject();
            ranked.RefreshLabels();

            // The base game shrinks the button row to 0.85 when the fifth (Dread) button is visible.
            // Shrink it a bit more for our extra button. Computed from scratch every time so it never compounds.
            if (parent is Control container)
            {
                int visible = parent.GetChildren().OfType<NSubmenuButton>().Count(b => b.Visible);
                var dread = Traverse.Create(__instance).Field("_dreadButton").GetValue<NSubmenuButton>();
                float baseScale = dread.Visible ? 0.85f : 1f;
                int without = Math.Max(1, visible - 1);
                container.Scale = Vector2.One * baseScale * Math.Min(1f, (float)without / visible * 1.08f);
            }
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Could not refresh Ranked button: {e}");
        }
    }
}
