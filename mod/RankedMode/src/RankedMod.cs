using HarmonyLib;
using MegaCrit.Sts2.Core.Debug;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Modding;

namespace RankedMode;

[ModInitializer(nameof(Init))]
public static class RankedMod
{
    public const string Id = "RankedMode";
    public const string Version = "0.4.1";

    public static string GameVersion => ReleaseInfoManager.Instance.ReleaseInfo?.Version ?? "unknown";
    public static string? GameCommit => ReleaseInfoManager.Instance.ReleaseInfo?.Commit;

    public static void Init()
    {
        RankedConfig.Load();
        RankedState.Load();
        new Harmony("ursumajor." + Id).PatchAll(typeof(RankedMod).Assembly);
        RankedLoc.Inject();
        Log.Info($"[RankedMode] v{Version} loaded. Server: {RankedConfig.ServerUrl}");
        // Retry any results that failed to upload last session (no UI this early).
        TaskHelper.RunSafely(RankedSubmitter.FlushPending(showToast: false));
    }
}
