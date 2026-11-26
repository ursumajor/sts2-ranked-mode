using System;
using System.IO;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.DevConsole.ConsoleCommands;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Saves;

namespace RankedMode;

/// <summary>
/// Unlocks everything on the (modded) profile so every ranked player generates the same run from the same seed
/// and character.
///
/// - Runs the game's own "unlock all" console command (cards, relics, potions, monsters, events, acts, timeline
///   epochs, ascension 10). New content added by game updates is covered automatically.
/// - Also marks every encounter as seen. The game forces a boss you've never seen into your run, so without this,
///   players with different histories would face different bosses on the same seed.
/// - Before the first unlock of a profile, copies its progress.save to ranked_mode/backups/.
/// - Repeated once per profile on every launch (harmless if everything is already unlocked) so content added by
///   game updates gets unlocked too.
///
/// The game keeps modded saves separate (…/modded/profileN), so the unmodded profile is never touched.
/// </summary>
internal static class RankedUnlocker
{
    // Profiles already handled this launch (the main menu is rebuilt after every run).
    private static readonly System.Collections.Generic.HashSet<int> _doneProfiles = new();

    public static void EnsureUnlocked()
    {
        if (!UserDataPathProvider.IsRunningModded)
        {
            Log.Warn("[RankedMode] Not on the modded save profile; skipping unlock-all.");
            return;
        }
        if (!_doneProfiles.Add(SaveManager.Instance.CurrentProfileId))
        {
            return;
        }
        try
        {
            var save = SaveManager.Instance;
            var progress = save.Progress;
            int epochsBefore = progress.Epochs.Count(e => e.State == EpochState.Revealed);

            BackupProgressOnce(save);

            var result = new UnlockConsoleCmd().Process(null, new[] { "all" });
            if (!result.success)
            {
                Log.Error($"[RankedMode] unlock all failed: {result.msg}");
            }

            int newlySeen = 0;
            foreach (var encounter in ModelDb.AllEncounters)
            {
                if (!progress.EncounterStats.ContainsKey(encounter.Id))
                {
                    progress.GetOrCreateEncounterStats(encounter.Id);
                    newlySeen++;
                }
            }
            save.SaveProgressFile();

            int epochsAfter = progress.Epochs.Count(e => e.State == EpochState.Revealed);
            Log.Info($"[RankedMode] Unlock-all applied (epochs revealed {epochsBefore} -> {epochsAfter}, encounters newly marked seen: {newlySeen}).");
        }
        catch (Exception e)
        {
            Log.Error($"[RankedMode] Unlock-all failed: {e}");
        }
    }

    private static void BackupProgressOnce(SaveManager save)
    {
        try
        {
            string source = ProjectSettings.GlobalizePath(save.GetProfileScopedPath("saves/progress.save"));
            if (!File.Exists(source))
            {
                return;
            }
            // One backup per profile path, taken before this mod ever unlocked it.
            string tag = Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(System.Text.Encoding.UTF8.GetBytes(source)))[..10];
            string dir = Path.Combine(RankedConfig.Dir, "backups");
            Directory.CreateDirectory(dir);
            string target = Path.Combine(dir, $"progress-before-unlock-{tag}.save");
            if (File.Exists(target))
            {
                return;
            }
            File.Copy(source, target);
            File.WriteAllText(target + ".txt", $"Backup of {source}\nTaken {DateTimeOffset.Now:O} before RankedMode unlocked everything.\n");
            Log.Info($"[RankedMode] Backed up progress to {target}");
        }
        catch (Exception e)
        {
            Log.Warn($"[RankedMode] Could not back up progress before unlocking: {e.Message}");
        }
    }
}
