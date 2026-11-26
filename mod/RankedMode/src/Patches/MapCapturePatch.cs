using System;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using HarmonyLib;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Saves.Runs;

namespace RankedMode.Patches;

/// <summary>
/// The game only saves the current act's map and clears the visited coordinates on every act change, so by the
/// end of a run earlier acts are gone. Every time the game builds a save (each room, and once more at run end),
/// record the current act's real map and path for the ranked run. The server's run viewer draws them.
/// </summary>
[HarmonyPatch(typeof(RunManager), nameof(RunManager.ToSave))]
internal static class MapCapturePatch
{
    private static void Postfix(SerializableRun __result)
    {
        try
        {
            if (__result == null || !RankedState.IsActiveSeed(__result.SerializableRng?.Seed))
            {
                return;
            }
            int act = __result.CurrentActIndex;
            var saved = act >= 0 && act < __result.Acts.Count ? __result.Acts[act].SavedMap : null;
            if (saved == null)
            {
                return;
            }
            string map = JsonSerializer.Serialize(saved, JsonSerializationUtility.GetTypeInfo<SerializableActMap>());
            string visited = new JsonArray(__result.VisitedMapCoords
                .Select(c => (JsonNode?)new JsonObject { ["col"] = c.col, ["row"] = c.row }).ToArray()).ToJsonString();
            RankedState.RecordAct(act, map, visited);
        }
        catch (Exception e)
        {
            Log.Warn($"[RankedMode] Could not record the map for the run viewer: {e.Message}");
        }
    }
}
