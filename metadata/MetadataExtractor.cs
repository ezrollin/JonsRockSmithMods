using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using RocksmithToolkitLib.PSARC;
using RocksmithToolkitLib;
using RocksmithToolkitLib.Sng2014HSL;

// Build for x86: the installed RocksmithToolkitLib.dll is a 32-bit assembly.
// This process opens archives for reading and inflates only small JSON manifests
// into memory. It never writes to Rocksmith, Steam, or the selected archive.
internal static class MetadataExtractor
{
    private sealed class Song
    {
        public string title;
        public string artist;
        public string song_key;
        public string dlc_key;
        public readonly SortedSet<string> arrangement_ids = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
        public readonly SortedSet<string> manifest_files = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
    }

    private sealed class ArrangementDifficulty
    {
        public string arrangement_id;
        public string arrangement_name;
        public SortedDictionary<string, int> phrase_max_difficulties =
            new SortedDictionary<string, int>(StringComparer.OrdinalIgnoreCase);
    }

    private static string toolkitDirectory;

    private static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        if (args.Length != 2 && args.Length != 3)
        {
            Console.Error.WriteLine("Usage: MetadataExtractor.exe <RocksmithToolkit-folder> <archive.psarc> | --difficulty <archive.psarc> | --health <archive.psarc>");
            return 2;
        }
        toolkitDirectory = Path.GetFullPath(args[0]);
        AppDomain.CurrentDomain.AssemblyResolve += ResolveToolkitAssembly;
        try
        {
            if (args.Length == 3 && String.Equals(args[1], "--difficulty", StringComparison.OrdinalIgnoreCase))
                return RunDifficulty(Path.GetFullPath(args[2]));
            if (args.Length == 3 && String.Equals(args[1], "--audio-inventory", StringComparison.OrdinalIgnoreCase))
                return RunAudioInventory(Path.GetFullPath(args[2]));
            if (args.Length == 3 && String.Equals(args[1], "--health", StringComparison.OrdinalIgnoreCase))
                return RunHealth(Path.GetFullPath(args[2]));
            if (args.Length != 2)
            {
                Console.Error.WriteLine("Usage: MetadataExtractor.exe <RocksmithToolkit-folder> <archive.psarc> | --difficulty <archive.psarc> | --health <archive.psarc>");
                return 2;
            }
            return Run(Path.GetFullPath(args[1]));
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }

    private static Assembly ResolveToolkitAssembly(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name + ".dll";
        string candidate = Path.Combine(toolkitDirectory, name);
        return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
    }

    private static int Run(string archivePath)
    {
        if (!File.Exists(archivePath) || !archivePath.EndsWith(".psarc", StringComparison.OrdinalIgnoreCase))
            throw new FileNotFoundException("PSARC archive missing", archivePath);
        if (!File.Exists(Path.Combine(toolkitDirectory, "RocksmithToolkitLib.dll")))
            throw new FileNotFoundException("RocksmithToolkitLib.dll missing", toolkitDirectory);

        var songs = new Dictionary<string, Song>(StringComparer.OrdinalIgnoreCase);
        var warnings = new List<string>();
        int manifestsScanned = 0;
        using (var stream = new FileStream(archivePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        using (var archive = new PSARC(true))
        {
            archive.Read(stream, true);
            foreach (var entry in archive.TOC)
            {
                string name = entry.Name ?? "";
                if (!name.StartsWith("manifests/", StringComparison.OrdinalIgnoreCase)
                    || !name.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
                    continue;
                if (entry.Length == 0 || entry.Length > 16 * 1024 * 1024)
                {
                    warnings.Add("Skipped manifest with invalid size: " + name);
                    continue;
                }
                try
                {
                    archive.InflateEntry(entry);
                    using (var reader = new StreamReader(entry.Data))
                    {
                        JObject document = JObject.Parse(reader.ReadToEnd());
                        var entries = document["Entries"] as JObject;
                        if (entries == null)
                        {
                            warnings.Add("No Entries object: " + name);
                            continue;
                        }
                        manifestsScanned++;
                        foreach (var arrangement in entries.Properties())
                        {
                            var attributes = arrangement.Value["Attributes"] as JObject;
                            if (attributes == null) continue;
                            string title = Value(attributes, "SongName");
                            string artist = Value(attributes, "ArtistName");
                            string songKey = Value(attributes, "SongKey");
                            string dlcKey = Value(attributes, "DLCKey");
                            string arrangementId = Value(attributes, "PersistentID");
                            if (String.IsNullOrWhiteSpace(arrangementId)) arrangementId = arrangement.Name;
                            if (String.IsNullOrWhiteSpace(title) || String.IsNullOrWhiteSpace(artist))
                            {
                                // Vocal manifests commonly omit display names while instrument
                                // arrangements in the same package supply them.
                                if (!String.Equals(Value(attributes, "ArrangementName"), "Vocals", StringComparison.OrdinalIgnoreCase))
                                    warnings.Add("Missing song title or artist: " + name + " / " + arrangement.Name);
                                continue;
                            }
                            // One package can have several songs and each song several arrangements.
                            // SongKey is package-defined; pair it with title/artist to avoid false merges.
                            string identity = String.Join("\u001f", songKey, artist, title);
                            Song song;
                            if (!songs.TryGetValue(identity, out song))
                            {
                                song = new Song
                                {
                                    title = title,
                                    artist = artist,
                                    song_key = songKey,
                                    dlc_key = dlcKey
                                };
                                songs.Add(identity, song);
                            }
                            if (!String.IsNullOrWhiteSpace(arrangementId)) song.arrangement_ids.Add(arrangementId);
                            song.manifest_files.Add(name);
                        }
                    }
                }
                catch (Exception error)
                {
                    warnings.Add("Failed " + name + ": " + error.GetType().Name + ": " + error.Message);
                }
                finally
                {
                    if (entry.Data != null)
                    {
                        entry.Data.Dispose();
                        entry.Data = null;
                    }
                }
            }
        }
        if (songs.Count == 0) warnings.Add("No readable song manifest found. Metadata remains unknown.");
        var result = new
        {
            archive_path = archivePath,
            file_name = Path.GetFileName(archivePath),
            manifest_count = manifestsScanned,
            song_count = songs.Count,
            songs = songs.Values.OrderBy(song => song.artist, StringComparer.OrdinalIgnoreCase)
                .ThenBy(song => song.title, StringComparer.OrdinalIgnoreCase)
                .Select(song => new
                {
                    song.title,
                    song.artist,
                    song.song_key,
                    song.dlc_key,
                    arrangement_ids = song.arrangement_ids.ToArray(),
                    manifest_files = song.manifest_files.ToArray()
                }).ToArray(),
            warnings = warnings.ToArray()
        };
        Console.WriteLine(JsonConvert.SerializeObject(result));
        return 0;
    }

    private static int RunDifficulty(string archivePath)
    {
        if (!File.Exists(archivePath) || !archivePath.EndsWith(".psarc", StringComparison.OrdinalIgnoreCase))
            throw new FileNotFoundException("PSARC archive missing", archivePath);
        if (!File.Exists(Path.Combine(toolkitDirectory, "RocksmithToolkitLib.dll")))
            throw new FileNotFoundException("RocksmithToolkitLib.dll missing", toolkitDirectory);

        var arrangements = new SortedDictionary<string, ArrangementDifficulty>(StringComparer.OrdinalIgnoreCase);
        var warnings = new List<string>();
        var entriesByPath = new Dictionary<string, Entry>(StringComparer.OrdinalIgnoreCase);
        int manifestsScanned = 0;
        using (var stream = new FileStream(archivePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        using (var archive = new PSARC(true))
        {
            archive.Read(stream, true);
            foreach (var entry in archive.TOC)
            {
                string normalized = NormalizeArchivePath(entry.Name);
                if (!String.IsNullOrEmpty(normalized) && !entriesByPath.ContainsKey(normalized))
                    entriesByPath.Add(normalized, entry);
            }

            foreach (var manifest in archive.TOC)
            {
                string manifestName = NormalizeArchivePath(manifest.Name);
                if (!manifestName.StartsWith("manifests/", StringComparison.OrdinalIgnoreCase)
                    || !manifestName.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
                    continue;
                if (manifest.Length == 0 || manifest.Length > 16 * 1024 * 1024)
                {
                    warnings.Add("Skipped manifest with invalid size: " + Path.GetFileName(manifestName));
                    continue;
                }

                try
                {
                    archive.InflateEntry(manifest);
                    JObject document;
                    using (var reader = new StreamReader(manifest.Data))
                        document = JObject.Parse(reader.ReadToEnd());
                    var manifestArrangements = document["Entries"] as JObject;
                    if (manifestArrangements == null)
                    {
                        warnings.Add("No Entries object: " + Path.GetFileName(manifestName));
                        continue;
                    }
                    manifestsScanned++;
                    var playableArrangements = manifestArrangements.Properties()
                        .Where(item =>
                        {
                            var attributes = item.Value["Attributes"] as JObject;
                            return attributes != null && IsPlayableArrangement(Value(attributes, "ArrangementName"));
                        })
                        .ToArray();
                    if (playableArrangements.Length == 0)
                        continue;

                    string sngPath = "songs/bin/generic/" + Path.GetFileNameWithoutExtension(manifestName) + ".sng";
                    Entry sngEntry;
                    if (!entriesByPath.TryGetValue(sngPath, out sngEntry))
                    {
                        warnings.Add("Missing arrangement SNG for manifest: " + Path.GetFileName(manifestName));
                        continue;
                    }

                    foreach (var manifestArrangement in playableArrangements)
                    {
                        var attributes = manifestArrangement.Value["Attributes"] as JObject;
                        if (attributes == null) continue;
                        string arrangementName = Value(attributes, "ArrangementName");
                        string arrangementId = Value(attributes, "PersistentID");
                        if (String.IsNullOrWhiteSpace(arrangementId)) arrangementId = manifestArrangement.Name;
                        if (String.IsNullOrWhiteSpace(arrangementId))
                        {
                            warnings.Add("Arrangement has no PersistentID: " + Path.GetFileName(manifestName));
                            continue;
                        }

                        ArrangementDifficulty parsed;
                        try
                        {
                            parsed = ReadArrangementDifficulty(archive, sngEntry, arrangementId, arrangementName);
                        }
                        catch (Exception error)
                        {
                            warnings.Add("Could not read SNG phrase maxima for " + Path.GetFileName(manifestName)
                                + ": " + error.GetType().Name + ": " + error.Message);
                            continue;
                        }

                        ArrangementDifficulty existing;
                        if (arrangements.TryGetValue(arrangementId, out existing))
                        {
                            if (!PhraseMaximaEqual(existing.phrase_max_difficulties, parsed.phrase_max_difficulties))
                                warnings.Add("Conflicting SNG phrase maxima for arrangement " + arrangementId);
                            continue;
                        }
                        arrangements.Add(arrangementId, parsed);
                    }
                }
                catch (Exception error)
                {
                    warnings.Add("Failed difficulty manifest " + Path.GetFileName(manifestName) + ": "
                        + error.GetType().Name + ": " + error.Message);
                }
                finally
                {
                    if (manifest.Data != null)
                    {
                        manifest.Data.Dispose();
                        manifest.Data = null;
                    }
                }
            }
        }

        var result = new
        {
            schema_version = 1,
            archive_path = archivePath,
            file_name = Path.GetFileName(archivePath),
            manifest_count = manifestsScanned,
            arrangements = arrangements.Values.ToArray(),
            warnings = warnings.ToArray()
        };
        Console.WriteLine(JsonConvert.SerializeObject(result));
        return 0;
    }

    private static ArrangementDifficulty ReadArrangementDifficulty(PSARC archive, Entry entry,
        string arrangementId, string arrangementName)
    {
        if (entry.Length == 0 || entry.Length > 64 * 1024 * 1024)
            throw new InvalidDataException("SNG size is outside the supported range");
        try
        {
            archive.InflateEntry(entry);
            Sng2014File sng;
            using (var stream = new MemoryStream())
            {
                entry.Data.CopyTo(stream);
                stream.Position = 0;
                sng = Sng2014File.ReadSng(stream, new Platform(GamePlatform.Pc, GameVersion.RS2014));
            }
            if (sng.Phrases == null || sng.Phrases.Phrases == null || sng.Phrases.Phrases.Length == 0)
                throw new InvalidDataException("SNG has no playable phrase section");

            var result = new ArrangementDifficulty
            {
                arrangement_id = arrangementId,
                arrangement_name = arrangementName
            };
            foreach (var phrase in sng.Phrases.Phrases)
            {
                string name = Encoding.UTF8.GetString(phrase.Name ?? new byte[0]).TrimEnd('\0').Trim();
                if (String.IsNullOrWhiteSpace(name))
                    throw new InvalidDataException("SNG contains an unnamed phrase");
                if (phrase.MaxDifficulty < 0)
                    throw new InvalidDataException("SNG contains a negative phrase maximum");
                int existingMaximum;
                if (result.phrase_max_difficulties.TryGetValue(name, out existingMaximum))
                {
                    if (existingMaximum != phrase.MaxDifficulty)
                        throw new InvalidDataException("Repeated phrase '" + name
                            + "' has conflicting maximum difficulties (" + existingMaximum
                            + " and " + phrase.MaxDifficulty + ")");
                    // A profile stores one record by phrase name. Equal maxima
                    // produce the same target for every occurrence.
                    continue;
                }
                result.phrase_max_difficulties.Add(name, phrase.MaxDifficulty);
            }
            return result;
        }
        finally
        {
            if (entry.Data != null)
            {
                entry.Data.Dispose();
                entry.Data = null;
            }
        }
    }



    private static int RunAudioInventory(string listPath)
    {
        if (new FileInfo(listPath).Length > 4 * 1024 * 1024) throw new InvalidDataException("Inventory input exceeds limit.");
        string[] paths = JsonConvert.DeserializeObject<string[]>(File.ReadAllText(listPath));
        if (paths == null || paths.Length > 5000) throw new InvalidDataException("Inventory archive count exceeds limit.");
        var banks = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
        var failures = new List<object>();
        foreach (string path in paths)
        {
            try
            {
                if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path) || !path.EndsWith(".psarc", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Inventory requires absolute .psarc paths.");
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                using (var archive = new PSARC(true))
                {
                    archive.Read(stream, true);
                    foreach (var entry in archive.TOC)
                    {
                        string name = NormalizeArchivePath(entry.Name);
                        if (name.Length > 1024) throw new InvalidDataException("Archive path exceeds inventory limit.");
                        if (name.StartsWith("audio/windows/", StringComparison.OrdinalIgnoreCase) && name.EndsWith(".bnk", StringComparison.OrdinalIgnoreCase)) banks.Add(name);
                        if (banks.Count > 20000) throw new ScanLimitException("Inventory bank count exceeds bounded limit.");
                    }
                }
            }
            catch (Exception error) { failures.Add(new { archive_path = path, detail = error.ToString() }); }
        }
        Console.WriteLine(JsonConvert.SerializeObject(new { schema_version = 1, requested_count = paths.Length, banks = banks.ToArray(), failures = failures.ToArray() }));
        return 0;
    }

    private sealed class ScanLimitException : IOException
    {
        public ScanLimitException(string message) : base(message) { }
    }

    private sealed class HealthIssue
    {
        public string severity;
        public string code;
        public string detail;
        public string path;
    }

    private static int RunHealth(string archivePath)
    {
        var issues = new List<HealthIssue>();
        var arrangements = new List<object>();
        var entries = new Dictionary<string, Entry>(StringComparer.OrdinalIgnoreCase);
        var banks = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        int manifestCount = 0;
        using (var stream = new FileStream(archivePath, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var archive = new PSARC(true))
        {
            archive.Read(stream, true);
            foreach (var entry in archive.TOC)
            {
                string name = NormalizeArchivePath(entry.Name);
                if (entries.ContainsKey(name)) AddHealth(issues, "error", "duplicate_entry", name, "Archive contains duplicate entry paths.");
                else entries.Add(name, entry);
            }
            foreach (var pair in entries)
            {
                if (!pair.Key.StartsWith("manifests/", StringComparison.OrdinalIgnoreCase) || !pair.Key.EndsWith(".json", StringComparison.OrdinalIgnoreCase)) continue;
                manifestCount++;
                try
                {
                    JObject document = JObject.Parse(Encoding.UTF8.GetString(ReadHealthEntry(archive, pair.Value, 16 * 1024 * 1024)));
                    var records = document["Entries"] as JObject;
                    if (records == null || !records.HasValues) throw new InvalidDataException("Manifest has no Entries records.");
                    foreach (var record in records.Properties())
                    {
                        var attributes = record.Value["Attributes"] as JObject;
                        if (attributes == null) throw new InvalidDataException("Manifest record has no Attributes.");
                        if (!IsPlayableArrangement(Value(attributes, "ArrangementName"))) continue;
                        string sngPath = "songs/bin/generic/" + Path.GetFileNameWithoutExtension(pair.Key) + ".sng";
                        Entry sngEntry;
                        long noteCount = 0;
                        if (!entries.TryGetValue(sngPath, out sngEntry))
                            AddHealth(issues, "error", "missing_chart", sngPath, "Instrumental arrangement SNG is missing.");
                        else
                        {
                            try
                            {
                                using (var data = new MemoryStream(ReadHealthEntry(archive, sngEntry, 64 * 1024 * 1024)))
                                {
                                    var sng = Sng2014File.ReadSng(data, new Platform(GamePlatform.Pc, GameVersion.RS2014));
                                    if (sng.Arrangements != null && sng.Arrangements.Arrangements != null)
                                        foreach (var level in sng.Arrangements.Arrangements)
                                            if (level.Notes != null) noteCount += level.Notes.Count;
                                }
                                if (noteCount == 0) AddHealth(issues, "error", "empty_chart", sngPath, "No instrumental note events found at any difficulty level.");
                            }
                            catch (Exception error) { AddHealth(issues, error is ScanLimitException ? "review" : "error", error is ScanLimitException ? "scan_limit" : "unreadable_chart", sngPath, error.ToString()); }
                        }
                        arrangements.Add(new { title = Value(attributes, "SongName"), artist = Value(attributes, "ArtistName"), song_key = Value(attributes, "SongKey"), arrangement_name = Value(attributes, "ArrangementName"), note_count = noteCount, song_bank = Value(attributes, "SongBank"), preview_bank = Value(attributes, "PreviewBankPath") });
                        foreach (string field in new[] { "SongBank", "PreviewBankPath" })
                        {
                            string bank = NormalizeArchivePath(Value(attributes, field));
                            if (String.IsNullOrWhiteSpace(bank)) { AddHealth(issues, "review", "missing_audio_reference", pair.Key, field + " is absent; shared dependency or malformed package needs review."); continue; }
                            if (bank.Split('/').Any(part => part == "..") || bank.Contains(":")) { AddHealth(issues, "error", "invalid_audio_reference", pair.Key, "Invalid audio reference: " + bank); continue; }
                            if (!bank.StartsWith("audio/", StringComparison.OrdinalIgnoreCase)) bank = "audio/windows/" + bank;
                            banks.Add(bank);
                        }
                    }
                }
                catch (Exception error) { AddHealth(issues, error is ScanLimitException ? "review" : "error", error is ScanLimitException ? "scan_limit" : "unreadable_manifest", pair.Key, error.ToString()); }
            }
            if (manifestCount == 0) AddHealth(issues, "review", "no_manifests", "", "No song manifests found. This can be a dependency archive; song playback was not verified.");
            if (manifestCount > 0 && arrangements.Count == 0) AddHealth(issues, "review", "no_instrumental_arrangements", "", "No recognized instrumental arrangements found; inspect package type.");
            var checkedWems = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string bank in banks)
            {
                Entry entry;
                if (!entries.TryGetValue(bank, out entry)) { AddHealth(issues, "review", "external_audio_bank", bank, "Referenced bank is not embedded. It may come from another installed archive; absence here does not prove corruption."); continue; }
                try
                {
                    byte[] bytes = ReadHealthEntry(archive, entry, 64 * 1024 * 1024);
                    int offset = 0;
                    bool hasIndex = false, hasHeader = false;
                    while (offset < bytes.Length)
                    {
                        if (bytes.Length - offset < 8) throw new InvalidDataException("Truncated soundbank chunk header.");
                        string chunk = Encoding.ASCII.GetString(bytes, offset, 4);
                        uint length = BitConverter.ToUInt32(bytes, offset + 4);
                        if (length > bytes.Length - offset - 8) throw new InvalidDataException("Soundbank chunk exceeds file size.");
                        if (chunk == "BKHD") hasHeader = true;
                        if (chunk == "DIDX")
                        {
                            hasIndex = true;
                            if (length % 12 != 0) throw new InvalidDataException("Invalid soundbank media index.");
                            for (int index = offset + 8; index < offset + 8 + length; index += 12)
                            {
                                string wem = "audio/windows/" + BitConverter.ToUInt32(bytes, index) + ".wem";
                                if (!checkedWems.Add(wem)) continue;
                                Entry media;
                                if (!entries.TryGetValue(wem, out media)) { AddHealth(issues, "review", "external_audio_media", wem, "Bank references media not embedded in this archive; verify shared dependencies before replacing or removing."); continue; }
                                try
                                {
                                    byte[] audio = ReadHealthEntry(archive, media, 256 * 1024 * 1024);
                                    if (audio.Length < 12 || (Encoding.ASCII.GetString(audio, 0, 4) != "RIFF" && Encoding.ASCII.GetString(audio, 0, 4) != "RIFX") || Encoding.ASCII.GetString(audio, 8, 4) != "WAVE")
                                        throw new InvalidDataException("Embedded WEM lacks a RIFF/RIFX WAVE header.");
                                }
                                catch (Exception error) { AddHealth(issues, error is ScanLimitException ? "review" : "error", error is ScanLimitException ? "scan_limit" : "unreadable_audio_media", wem, error.ToString()); }
                            }
                        }
                        offset += 8 + (int)length;
                    }
                    if (!hasHeader) throw new InvalidDataException("Soundbank lacks BKHD header.");
                    if (!hasIndex) AddHealth(issues, "review", "unverified_audio_references", bank, "No DIDX media index; this soundbank layout requires playback/dependency verification.");
                }
                catch (Exception error) { AddHealth(issues, error is ScanLimitException ? "review" : "error", error is ScanLimitException ? "scan_limit" : "unreadable_audio_bank", bank, error.ToString()); }
            }
        }
        string status = issues.Any(item => item.severity == "error") ? "issue_found" : issues.Count > 0 ? "needs_review" : "no_issue_found";
        Console.WriteLine(JsonConvert.SerializeObject(new { schema_version = 1, archive_path = archivePath, manifest_count = manifestCount, status = status, arrangements = arrangements.ToArray(), issues = issues.ToArray() }));
        return 0;
    }

    private static void AddHealth(List<HealthIssue> issues, string severity, string code, string path, string detail)
    {
        if (!issues.Any(item => item.code == code && item.path == path)) issues.Add(new HealthIssue { severity = severity, code = code, path = path, detail = detail });
    }

    private static byte[] ReadHealthEntry(PSARC archive, Entry entry, long maximum)
    {
        if (entry.Length == 0) throw new InvalidDataException("Empty archive entry.");
        if (entry.Length > (ulong)maximum) throw new ScanLimitException("Entry exceeds bounded scan limit; not classified as corrupt: " + entry.Length);
        try
        {
            archive.InflateEntry(entry);
            using (var output = new MemoryStream()) { entry.Data.CopyTo(output); return output.ToArray(); }
        }
        finally { if (entry.Data != null) { entry.Data.Dispose(); entry.Data = null; } }
    }

    private static bool IsPlayableArrangement(string arrangementName)
    {
        string name = (arrangementName ?? "").Trim();
        return name.StartsWith("Lead", StringComparison.OrdinalIgnoreCase)
            || name.StartsWith("Rhythm", StringComparison.OrdinalIgnoreCase)
            || name.StartsWith("Bass", StringComparison.OrdinalIgnoreCase)
            || name.StartsWith("Combo", StringComparison.OrdinalIgnoreCase);
    }

    private static bool PhraseMaximaEqual(IDictionary<string, int> left, IDictionary<string, int> right)
    {
        if (left.Count != right.Count) return false;
        foreach (var item in left)
        {
            int value;
            if (!right.TryGetValue(item.Key, out value) || value != item.Value) return false;
        }
        return true;
    }

    private static string NormalizeArchivePath(string path)
    {
        return (path ?? "").Replace('\\', '/').TrimStart('/');
    }

    private static string Value(JObject attributes, string name)
    {
        JToken value;
        return attributes.TryGetValue(name, StringComparison.OrdinalIgnoreCase, out value)
            ? ((string)value ?? "").Trim() : "";
    }
}
