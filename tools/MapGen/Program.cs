// MapGen: rebuild a run's act maps from its seed using the game's own map generator, without running the game.
//
//   mapgen <run.json>            print {"acts":[<saved_map>, ...]} for every act of the run
//   mapgen --verify <run.json>   compare the regenerated map of the run's current act with its saved_map
//
// <run.json> is a SerializableRun (a current_run.save, or the run JSON the ranked server stores per run).
// Inputs used: rng.seed, ascension, acts[i].id and acts[i].rooms.second_boss_id. Singleplayer only.
//
// Map generation changes between game versions, so build this against the same game version the run used.

using System;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Ascension;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Random;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Saves.Runs;

bool verify = args.Length == 2 && args[0] == "--verify";
if (args.Length != 1 && !verify)
{
    Console.Error.WriteLine("usage: mapgen [--verify] <run.json>");
    return 2;
}
var run = JsonNode.Parse(System.IO.File.ReadAllText(args[^1]))!;
Init(run["ascension"]?.GetValue<int>() ?? 0);

string seed = run["rng"]!["seed"]!.GetValue<string>();
var acts = run["acts"]!.AsArray();
var maps = new JsonArray();
for (int i = 0; i < acts.Count; i++)
{
    maps.Add(JsonNode.Parse(Generate(seed, i, acts[i]!)));
}

if (!verify)
{
    Console.WriteLine(new JsonObject { ["seed"] = seed, ["acts"] = maps }.ToJsonString());
    return 0;
}

int current = run["current_act_index"]?.GetValue<int>() ?? 0;
var saved = acts[current]!["saved_map"];
if (saved == null)
{
    Console.Error.WriteLine("run has no saved_map for its current act");
    return 2;
}
string Canon(JsonNode m) => string.Join("|", m["points"]!.AsArray()
    .Select(p => $"{p!["coord"]!["col"]},{p["coord"]!["row"]}:{p["type"]}>" +
                 string.Join(";", (p["children"]?.AsArray() ?? new JsonArray()).Select(c => $"{c!["col"]},{c["row"]}").OrderBy(x => x)))
    .OrderBy(x => x)) + $"|boss {m["boss"]!["coord"]!["col"]},{m["boss"]!["coord"]!["row"]}";
bool same = Canon(saved) == Canon(maps[current]!);
Console.WriteLine(same ? $"OK: act {current + 1} regenerated identically" : $"MISMATCH: act {current + 1} differs");
return same ? 0 : 1;

static string Generate(string seed, int actIndex, JsonNode act)
{
    var model = ModelDb.GetById<ActModel>(ModelId.Deserialize(act["id"]!.GetValue<string>()));
    bool secondBoss = act["rooms"]?["second_boss_id"] is JsonValue v && v.GetValue<string>() is { Length: > 0 };
    // Same call as StandardActMap.CreateFor (singleplayer, no treasure->elite swap).
    var map = new StandardActMap(new Rng(new RunRngSet(seed).Seed, $"act_{actIndex + 1}_map"), model,
        isMultiplayer: false, shouldReplaceTreasureWithElites: false, hasSecondBoss: secondBoss);
    return JsonSerializer.Serialize(SerializableActMap.FromActMap(map), JsonSerializationUtility.GetTypeInfo<SerializableActMap>());
}

static void Init(int ascension)
{
    AscensionPatch.Level = ascension;
    new Harmony("mapgen").PatchAll(typeof(AscensionPatch).Assembly);
    MegaCrit.Sts2.Core.Modding.AssemblyInfo.Init();
    // Only the act models: several other models touch the Godot engine in their constructors.
    var actTypes = typeof(ActModel).Assembly.GetTypes()
        .Where(t => !t.IsAbstract && typeof(ActModel).IsAssignableFrom(t) && t.GetConstructor(Type.EmptyTypes) != null)
        .ToArray();
    ModelDb.Init(actTypes);
}

/// <summary>Map generation asks the run in progress for its ascension (e.g. Swarming Elites adds elites).</summary>
[HarmonyPatch(typeof(RunManager), nameof(RunManager.HasAscension))]
static class AscensionPatch
{
    public static int Level;

    static bool Prefix(AscensionLevel level, ref bool __result)
    {
        __result = level != AscensionLevel.None && (int)level <= Level;
        return false;
    }
}
