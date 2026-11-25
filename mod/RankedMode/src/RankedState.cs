using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Logging;

namespace RankedMode;

internal sealed class ActiveRun
{
    public string RunId = "";
    public string Seed = "";
    public string WindowId = "";
    public long StartedAt;
}

internal sealed class PendingSubmission
{
    public string RunId = "";
    public string Payload = "";
    public int Attempts;
}

/// <summary>
/// Persistent local state: player credentials, the ranked run in progress, results waiting to upload,
/// and every ranked seed this install has played (so they stay hidden in run history).
/// Stored at &lt;user data&gt;/ranked_mode/state.json.
/// </summary>
internal static class RankedState
{
    private static readonly object Lock = new();

    public static string? Token { get; private set; }
    public static string? TokenServer { get; private set; }
    public static ActiveRun? Active { get; private set; }
    public static readonly HashSet<string> KnownSeeds = new();
    public static readonly List<PendingSubmission> Pending = new();

    private static string FilePath => Path.Combine(RankedConfig.Dir, "state.json");

    public static bool IsRankedSeed(string? seed) => seed != null && KnownSeeds.Contains(seed);

    public static bool IsActiveSeed(string? seed) => seed != null && Active != null && Active.Seed == seed;

    public static bool HasCredentialsFor(string server) => Token != null && TokenServer == server;

    public static void SetCredentials(string server, string token)
    {
        lock (Lock)
        {
            Token = token;
            TokenServer = server;
            Save();
        }
    }

    public static void BeginRun(string runId, string seed, string windowId)
    {
        lock (Lock)
        {
            Active = new ActiveRun
            {
                RunId = runId,
                Seed = seed,
                WindowId = windowId,
                StartedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            };
            KnownSeeds.Add(seed);
            Save();
        }
    }

    /// <summary>Queues a result for upload and clears the active run in one save.</summary>
    public static void EnqueueSubmission(string runId, string payload)
    {
        lock (Lock)
        {
            if (Pending.All(p => p.RunId != runId))
            {
                Pending.Add(new PendingSubmission { RunId = runId, Payload = payload });
            }
            if (Active?.RunId == runId)
            {
                Active = null;
            }
            Save();
        }
    }

    public static void ClearActive()
    {
        lock (Lock)
        {
            Active = null;
            Save();
        }
    }

    public static void RemovePending(PendingSubmission p)
    {
        lock (Lock)
        {
            Pending.Remove(p);
            Save();
        }
    }

    public static void BumpAttempts(PendingSubmission p)
    {
        lock (Lock)
        {
            p.Attempts++;
            Save();
        }
    }

    public static void Load()
    {
        lock (Lock)
        {
            try
            {
                if (!File.Exists(FilePath))
                {
                    return;
                }
                var root = JsonNode.Parse(File.ReadAllText(FilePath))?.AsObject();
                if (root == null)
                {
                    return;
                }
                Token = root["token"]?.GetValue<string>();
                TokenServer = root["token_server"]?.GetValue<string>();
                if (root["active"] is JsonObject a)
                {
                    Active = new ActiveRun
                    {
                        RunId = a["run_id"]?.GetValue<string>() ?? "",
                        Seed = a["seed"]?.GetValue<string>() ?? "",
                        WindowId = a["window_id"]?.GetValue<string>() ?? "",
                        StartedAt = a["started_at"]?.GetValue<long>() ?? 0,
                    };
                }
                if (root["known_seeds"] is JsonArray seeds)
                {
                    foreach (var s in seeds)
                    {
                        if (s?.GetValue<string>() is { } str)
                        {
                            KnownSeeds.Add(str);
                        }
                    }
                }
                if (root["pending"] is JsonArray pending)
                {
                    foreach (var p in pending.OfType<JsonObject>())
                    {
                        Pending.Add(new PendingSubmission
                        {
                            RunId = p["run_id"]?.GetValue<string>() ?? "",
                            Payload = p["payload"]?.GetValue<string>() ?? "",
                            Attempts = p["attempts"]?.GetValue<int>() ?? 0,
                        });
                    }
                }
            }
            catch (Exception e)
            {
                Log.Error($"[RankedMode] Could not read ranked state: {e.Message}");
            }
        }
    }

    private static void Save()
    {
        try
        {
            Directory.CreateDirectory(RankedConfig.Dir);
            var root = new JsonObject
            {
                ["token"] = Token,
                ["token_server"] = TokenServer,
                ["active"] = Active == null
                    ? null
                    : new JsonObject
                    {
                        ["run_id"] = Active.RunId,
                        ["seed"] = Active.Seed,
                        ["window_id"] = Active.WindowId,
                        ["started_at"] = Active.StartedAt,
                    },
                ["known_seeds"] = new JsonArray(KnownSeeds.Select(s => (JsonNode?)JsonValue.Create(s)).ToArray()),
                ["pending"] = new JsonArray(Pending.Select(p => (JsonNode?)new JsonObject
                {
                    ["run_id"] = p.RunId,
                    ["payload"] = p.Payload,
                    ["attempts"] = p.Attempts,
                }).ToArray()),
            };
            var tmp = FilePath + ".tmp";
            File.WriteAllText(tmp, root.ToJsonString());
            File.Move(tmp, FilePath, overwrite: true);
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Could not save ranked state: {e.Message}");
        }
    }
}
