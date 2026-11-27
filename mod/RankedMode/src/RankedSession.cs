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

    /// <summary>
    /// The screen is showing the countdown to the next window (no window open yet). Embark is hidden; when the
    /// countdown reaches zero the screen checks with the server and switches to the open state in place.
    /// </summary>
    public static bool Closed { get; private set; }

    /// <summary>Closed state: when the next window opens (server time, unix seconds).</summary>
    public static long OpensAt { get; private set; }

    /// <summary>Closed state: a message to show instead of the countdown (e.g. "already played").</summary>
    public static string? Notice { get; set; }

    /// <summary>Closed state: a server check is in flight / don't check again before this time.</summary>
    public static bool Refreshing { get; set; }
    public static long RetryAt { get; set; }
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

    public static void BeginClosedScreen(string character, long opensAt, long serverOffset)
    {
        BeginScreen(windowId: "", character, endsAt: 0, serverOffset);
        Closed = true;
        OpensAt = opensAt;
    }

    public static void BeginScreen(string windowId, string character, long endsAt, long serverOffset)
    {
        ScreenActive = true;
        Closed = false;
        OpensAt = 0;
        Notice = null;
        Refreshing = false;
        RetryAt = 0;
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
        Closed = false;
        Notice = null;
        Refreshing = false;
        WindowId = null;
        Character = null;
        SecretSeed = null;
        EmbarkInFlight = false;
        BypassEmbarkPatch = false;
    }
}
