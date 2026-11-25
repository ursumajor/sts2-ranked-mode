using System;
using System.IO;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Logging;

namespace RankedMode;

/// <summary>
/// User-editable config at &lt;user data&gt;/ranked_mode/config.json
/// (Linux: ~/.local/share/SlayTheSpire2/ranked_mode/config.json).
/// The RANKED_SERVER_URL environment variable overrides the file.
/// </summary>
internal static class RankedConfig
{
    public const string DefaultServerUrl = "http://127.0.0.1:8000";

    public static string ServerUrl { get; private set; } = DefaultServerUrl;

    public static string Dir => Path.Combine(OS.GetUserDataDir(), "ranked_mode");

    private static string FilePath => Path.Combine(Dir, "config.json");

    public static void Load()
    {
        try
        {
            Directory.CreateDirectory(Dir);
            if (!File.Exists(FilePath))
            {
                File.WriteAllText(FilePath, new JsonObject { ["server_url"] = DefaultServerUrl }.ToJsonString(new() { WriteIndented = true }));
            }
            var node = JsonNode.Parse(File.ReadAllText(FilePath));
            var url = node?["server_url"]?.GetValue<string>();
            if (!string.IsNullOrWhiteSpace(url))
            {
                ServerUrl = url.Trim().TrimEnd('/');
            }
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Could not read {FilePath}: {e.Message}. Using {ServerUrl}");
        }

        var env = System.Environment.GetEnvironmentVariable("RANKED_SERVER_URL");
        if (!string.IsNullOrWhiteSpace(env))
        {
            ServerUrl = env.Trim().TrimEnd('/');
        }
    }
}
