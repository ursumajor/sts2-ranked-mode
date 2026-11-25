using System; using System.Linq; using HarmonyLib;
var asm = typeof(RankedMode.RankedMod).Assembly;
var harmony = new Harmony("check");
int ok = 0, bad = 0;
foreach (var t in AccessTools.GetTypesFromAssembly(asm).Where(t => t.GetCustomAttributes(typeof(HarmonyPatch), true).Any() ))
{
    try { var patched = harmony.CreateClassProcessor(t).Patch(); foreach (var m in patched) { Console.WriteLine($"OK   {t.Name} -> {m.DeclaringType?.Name}.{m.Name}"); ok++; } }
    catch (Exception e) { Console.WriteLine($"FAIL {t.Name}: {e.GetBaseException().Message}"); bad++; }
}
Console.WriteLine($"{ok} patched, {bad} failed");
return bad == 0 ? 0 : 1;
