using System;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;

namespace RankedMode;

/// <summary>Builds the end-of-run payload and uploads it, with a persistent retry queue.</summary>
internal static class RankedSubmitter
{
    private static bool _flushing;

    // The game's .run history file for the ranked run, captured when the game saves it (see RunFileCapturePatch).
    private static string? _runFileSeed;
    private static string? _runFile;

    public static void CaptureRunFile(string seed, string content)
    {
        _runFileSeed = seed;
        _runFile = content;
    }

    public static void Submit(SerializableRun run, bool victory, bool abandoned)
    {
        var active = RankedState.Active;
        if (active == null || run.SerializableRng?.Seed != active.Seed)
        {
            return;
        }
        try
        {
            string runJson = JsonSerializationUtility.ToJson(run);
            int score = ScoreUtility.CalculateDailyScore(run, run.Players[0].NetId, victory);
            var payload = new JsonObject
            {
                ["victory"] = victory,
                ["abandoned"] = abandoned,
                ["score"] = score,
                ["game_version"] = RankedMod.GameVersion,
                ["game_commit"] = RankedMod.GameCommit,
                ["mod_version"] = RankedMod.Version,
                ["run"] = JsonNode.Parse(runJson),
            };
            if (_runFile != null && _runFileSeed == active.Seed)
            {
                payload["run_file"] = _runFile;
            }
            else
            {
                Log.Warn("[RankedMode] No .run history file was captured for this ranked run; submitting without it.");
            }
            _runFile = null;
            _runFileSeed = null;
            var acts = new JsonObject();
            foreach (var (act, entry) in RankedState.MapLog)
            {
                acts[act.ToString()] = new JsonObject { ["map"] = JsonNode.Parse(entry.Map), ["visited"] = JsonNode.Parse(entry.Visited) };
            }
            if (acts.Count > 0)
            {
                payload["map_log"] = new JsonObject { ["acts"] = acts };
            }
            RankedState.EnqueueSubmission(active.RunId, payload.ToJsonString());
            Log.Info($"[RankedMode] Queued ranked result (victory={victory}, abandoned={abandoned}).");
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Failed to build ranked result: {e}");
            return;
        }
        TaskHelper.RunSafely(FlushPending(showToast: true));
    }

    public static async Task FlushPending(bool showToast)
    {
        if (_flushing || RankedState.Pending.Count == 0 || RankedState.Token == null)
        {
            return;
        }
        _flushing = true;
        try
        {
            foreach (var pending in RankedState.Pending.ToList())
            {
                try
                {
                    JsonNode res = await RankedApi.Finish(pending.RunId, pending.Payload);
                    RankedState.RemovePending(pending);
                    bool valid = res["valid"]?.GetValue<bool>() ?? false;
                    string msg = valid
                        ? $"Ranked result submitted: rank #{res["rank"]}"
                        : $"Ranked result submitted but flagged ({string.Join(", ", (res["flags"] as JsonArray ?? new JsonArray()).Select(f => f?.ToString()))})";
                    Log.Info("[RankedMode] " + msg);
                    if (showToast)
                    {
                        RankedUi.Toast(msg);
                    }
                }
                catch (RankedApiException e) when (e.Status is >= 400 and < 500)
                {
                    // The server will never accept this one (already submitted, wrong seed, unknown run...).
                    Log.Error($"[RankedMode] Ranked result rejected ({e.Status}): {e.Message}");
                    RankedState.RemovePending(pending);
                    if (showToast)
                    {
                        RankedUi.Toast("Ranked result rejected: " + e.Message);
                    }
                }
                catch (Exception e)
                {
                    RankedState.BumpAttempts(pending);
                    Log.Warn($"[RankedMode] Ranked upload failed, will retry later: {RankedApi.Describe(e)}");
                    if (showToast)
                    {
                        RankedUi.Toast("Ranked server unreachable. Your result will be uploaded later.");
                    }
                }
            }
        }
        finally
        {
            _flushing = false;
        }
    }
}
