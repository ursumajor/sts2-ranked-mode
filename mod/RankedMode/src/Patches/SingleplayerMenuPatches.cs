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

    // Bright golden yellow on the submenu panel texture (Daily uses h 0.48, Dread 0.75, Custom 0.93).
    private const float GoldHue = 0.94f;
    private const float GoldSaturation = 2.8f;
    private const float GoldBrightness = 0.95f;

    /// <summary>File name of the optional custom card picture, next to RankedMode.dll in the mods folder.</summary>
    public const string IconFile = "ranked_icon.png";

    private static Texture2D? _customIcon;

    /// <summary>
    /// Loads mods/RankedMode/ranked_icon.png if present. The game's card pictures are 378x288 and sit inside a
    /// decorative frame, so an image of that size (or the same 21:16 shape) fits best. Returns null to keep the
    /// game's trophy picture.
    /// </summary>
    private static Texture2D? LoadCustomIcon()
    {
        if (_customIcon != null)
        {
            return _customIcon;
        }
        try
        {
            string dir = System.IO.Path.GetDirectoryName(typeof(RankedMod).Assembly.Location) ?? "";
            string path = System.IO.Path.Combine(dir, IconFile);
            if (!System.IO.File.Exists(path))
            {
                return null;
            }
            var image = Image.LoadFromFile(path);
            if (image == null || image.IsEmpty())
            {
                Log.Warn($"[RankedMode] Could not read {path}; using the default icon.");
                return null;
            }
            _customIcon = ImageTexture.CreateFromImage(image);
            Log.Info($"[RankedMode] Using custom Ranked icon {path} ({image.GetWidth()}x{image.GetHeight()}).");
            return _customIcon;
        }
        catch (Exception e)
        {
            Log.Warn($"[RankedMode] Custom Ranked icon failed to load: {e.Message}");
            return null;
        }
    }

    private const string TrophyPath = "res://images/packed/main_menu/submenu_trophy_icon.png";
    private static Texture2D? _trophyIcon;

    /// <summary>
    /// The game's gold trophy icon, made into a full card picture. The icon is small and transparent (280x151),
    /// and the card portrait fills its box, which would crop it and put gold on gold. So build a 378x288 picture
    /// (the game's card picture size): a navy backdrop with a soft warm glow and the whole trophy centred.
    /// </summary>
    private static Texture2D? BuildTrophyIcon()
    {
        if (_trophyIcon != null)
        {
            return _trophyIcon;
        }
        try
        {
            var source = ResourceLoader.Load<Texture2D>(TrophyPath)?.GetImage();
            if (source == null)
            {
                return null;
            }
            if (source.IsCompressed())
            {
                source.Decompress();
            }
            source.Convert(Image.Format.Rgba8);

            const int width = 378, height = 288;
            var picture = Image.CreateEmpty(width, height, false, Image.Format.Rgba8);
            var navy = new Color(26 / 255f, 26 / 255f, 46 / 255f);
            var glow = new Color(1f, 0.84f, 0.47f);
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    float dx = (x - width / 2f) / (width * 0.55f), dy = (y - height / 2f) / (height * 0.55f);
                    float g = Mathf.Clamp(1f - Mathf.Sqrt(dx * dx + dy * dy), 0f, 1f);
                    picture.SetPixel(x, y, navy.Lerp(glow, g * g * 0.35f));
                }
            }
            float scale = Mathf.Min(width * 0.86f / source.GetWidth(), height * 0.86f / source.GetHeight());
            int tw = (int)(source.GetWidth() * scale), th = (int)(source.GetHeight() * scale);
            source.Resize(tw, th, Image.Interpolation.Lanczos);
            picture.BlendRect(source, new Rect2I(0, 0, tw, th), new Vector2I((width - tw) / 2, (height - th) / 2));
            _trophyIcon = ImageTexture.CreateFromImage(picture);
            return _trophyIcon;
        }
        catch (Exception e)
        {
            Log.Warn($"[RankedMode] Could not build the trophy card picture: {e.Message}");
            return null;
        }
    }

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
            if ((LoadCustomIcon() ?? BuildTrophyIcon()) is { } icon)
            {
                ranked.GetNodeOrNull<TextureRect>("%SubmenuPortrait")?.SetTexture(icon);
            }
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
