using System;

namespace RankedMode;

/// <summary>
/// In-memory state while the Dread screen is being used for a ranked run.
///
/// The window's character is public: the screen locks selection to it. The secret run seed is only fetched
/// when the player presses Embark, which also uses up their one attempt. A player can't learn the seed and then
/// back out to scout it in Custom mode.
/// </summary>
internal static class RankedSession
{
    public static bool ScreenActive { get; private set; }
    public static string? WindowId { get; private set; }

    /// <summary>Model id of the character everyone plays in this window, e.g. "CHARACTER.IRONCLAD".</summary>
    public static string? Character { get; private set; }

    public static long WindowEndsAt { get; private set; }

    /// <summary>server_time - local time, in seconds.</summary>
    public static long ServerOffset { get; private set; }

    public static string? SecretSeed { get; set; }
    public static bool EmbarkInFlight { get; set; }
    public static bool BypassEmbarkPatch { get; set; }

    /// <summary>Set right before the game would upload a ranked run to the official Steam daily leaderboard.</summary>
    public static bool SuppressNextDailyUpload { get; set; }

    public static long ServerNow => DateTimeOffset.UtcNow.ToUnixTimeSeconds() + ServerOffset;

    public static void BeginScreen(string windowId, string character, long endsAt, long serverOffset)
    {
        ScreenActive = true;
        WindowId = windowId;
        Character = character;
        WindowEndsAt = endsAt;
        ServerOffset = serverOffset;
        SecretSeed = null;
        EmbarkInFlight = false;
        BypassEmbarkPatch = false;
    }

    public static void EndScreen()
    {
        ScreenActive = false;
        WindowId = null;
        Character = null;
        SecretSeed = null;
        EmbarkInFlight = false;
        BypassEmbarkPatch = false;
    }
}
