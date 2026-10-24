using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using RocksmithToolkitLib;
using RocksmithToolkitLib.DLCPackage;
using RocksmithToolkitLib.DLCPackage.AggregateGraph2014;
using RocksmithToolkitLib.PSARC;

internal sealed class SelectedSong
{
    public string artist { get; set; }
    public string title { get; set; }
    public string song_key { get; set; }
    public string dlc_key { get; set; }
    public string[] manifest_files { get; set; }
}
internal sealed class ResultFile
{
    public string artist { get; set; }
    public string title { get; set; }
    public string song_key { get; set; }
    public string dlc_key { get; set; }
    public string path { get; set; }
    public long bytes { get; set; }
    public string sha256 { get; set; }
}
internal sealed class ResultDoc
{
    public string source { get; set; }
    public string source_sha256_before { get; set; }
    public string source_sha256_after { get; set; }
    public string output_directory { get; set; }
    public int selected_song_count { get; set; }
    public List<ResultFile> outputs { get; set; }
    public string[] warnings { get; set; }
}
internal static class SplitPackHelper
{
    private static string toolkitDirectory;
    private static string activeJobRoot;
    private static readonly StringComparer PathComparer = StringComparer.OrdinalIgnoreCase;

    private static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        if (args.Length != 4)
        {
            Console.Error.WriteLine("Usage: SplitPackHelper.exe <Toolkit-folder> <source.psarc> <selection.json> <output-directory>");
            return 2;
        }
        toolkitDirectory = Path.GetFullPath(args[0]);
        AppDomain.CurrentDomain.AssemblyResolve += ResolveToolkitAssembly;
        try { return Run(Path.GetFullPath(args[1]), Path.GetFullPath(args[2]), Path.GetFullPath(args[3])); }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.ToString());
            return 1;
        }
        finally
        {
            if (!String.IsNullOrWhiteSpace(activeJobRoot) && Directory.Exists(activeJobRoot))
            {
                try { Directory.Delete(activeJobRoot, true); }
                catch (Exception cleanupError) { Console.Error.WriteLine("Temporary split workspace cleanup failed: " + cleanupError.Message); }
            }
        }
    }

    private static Assembly ResolveToolkitAssembly(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name + ".dll";
        string candidate = Path.Combine(toolkitDirectory, name);
        return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
    }

    private static int Run(string source, string selectionPath, string outputRoot)
    {
        if (!File.Exists(source) || !source.EndsWith(".psarc", StringComparison.OrdinalIgnoreCase))
            throw new FileNotFoundException("Source PSARC does not exist", source);
        if (!File.Exists(Path.Combine(toolkitDirectory, "RocksmithToolkitLib.dll")))
            throw new FileNotFoundException("RocksmithToolkitLib.dll is missing", toolkitDirectory);
        if (!File.Exists(selectionPath)) throw new FileNotFoundException("Song selection file is missing", selectionPath);
        var songs = JsonConvert.DeserializeObject<List<SelectedSong>>(File.ReadAllText(selectionPath));
        if (songs == null || songs.Count == 0) throw new InvalidDataException("Select at least one song to keep.");
        Directory.CreateDirectory(outputRoot);
        if (Directory.GetFileSystemEntries(outputRoot).Length != 0)
            throw new IOException("Split output directory must be empty. Choose a new folder; no files were overwritten.");

        ConfigureExternalApps();
        string before = HashFile(source);
        PreflightEmbeddedAudio(source, songs);
        string jobId = Guid.NewGuid().ToString("N");
        string jobRoot = Path.Combine(Path.GetTempPath(), "JRS_Split_" + jobId);
        activeJobRoot = jobRoot;
        string unpacked = Path.Combine(jobRoot, Path.GetFileNameWithoutExtension(source) + "_RS2014_Pc");
        ExtractSourceArchive(source, unpacked);
        string graphPath = SingleFile(unpacked, "*.nt", "aggregate graph");
        string hsanPath = SingleFile(unpacked, "*.hsan", "song index");
        string appIdPath = Path.Combine(unpacked, "appid.appid");
        if (!File.Exists(appIdPath)) throw new InvalidDataException("Source package has no appid.appid.");
        string appId = File.ReadAllText(appIdPath).Trim();
        if (appId.Length != 6 || appId.Any(c => !Char.IsDigit(c)))
            throw new InvalidDataException("Source app ID is not a six digit value; refusing to invent a replacement.");

        var outputs = new List<ResultFile>();
        var selectedIdentities = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        int outputIndex = 0;
        foreach (var selection in songs)
        {
            outputIndex++;
            if (String.IsNullOrWhiteSpace(selection.song_key) || String.IsNullOrWhiteSpace(selection.artist)
                || String.IsNullOrWhiteSpace(selection.title) || selection.manifest_files == null
                || selection.manifest_files.Length == 0)
                throw new InvalidDataException("A selected song has incomplete artist, title, SongKey, or manifest data.");
            string identity = String.Join("\u001f", selection.dlc_key ?? String.Empty, selection.song_key,
                selection.artist, selection.title);
            if (!selectedIdentities.Add(identity))
                throw new InvalidDataException("The same song was selected more than once: " + selection.artist + " - " + selection.title);
            var manifestPaths = selection.manifest_files.Select(path => ResolveInside(unpacked, path)).Distinct(PathComparer).ToArray();
            if (manifestPaths.Any(path => !File.Exists(path))) throw new FileNotFoundException("A selected source manifest is missing.");
            string requestedPrefix = CommonStemPrefix(manifestPaths.Select(Path.GetFileNameWithoutExtension));
            if (String.IsNullOrWhiteSpace(requestedPrefix)) throw new InvalidDataException("Could not identify the selected song asset prefix for " + selection.title);
            string vocalManifest = Path.Combine(Path.GetDirectoryName(manifestPaths[0]), requestedPrefix + "_vocals.json");
            if (File.Exists(vocalManifest) && !manifestPaths.Contains(vocalManifest, PathComparer))
                manifestPaths = manifestPaths.Concat(new[] { vocalManifest }).ToArray();
            var arrangementIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var manifestStems = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var albumArtSlugs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var manifestPath in manifestPaths)
            {
                var stem = Path.GetFileNameWithoutExtension(manifestPath);
                manifestStems.Add(stem);
                var doc = JObject.Parse(File.ReadAllText(manifestPath));
                var entries = doc["Entries"] as JObject;
                if (entries == null || entries.Properties().Count() == 0)
                    throw new InvalidDataException("Selected manifest has no arrangement entries: " + Path.GetFileName(manifestPath));
                foreach (var entry in entries.Properties())
                {
                    var attr = entry.Value["Attributes"] as JObject;
                    if (attr == null) throw new InvalidDataException("Manifest arrangement has no Attributes: " + entry.Name);
                    string artist = Value(attr, "ArtistName");
                    string title = Value(attr, "SongName");
                    string key = Value(attr, "SongKey");
                    string dlcKey = Value(attr, "DLCKey");
                    bool unlabeledVocals = EqualsCi(Value(attr, "ArrangementName"), "Vocals")
                        && String.IsNullOrWhiteSpace(artist) && String.IsNullOrWhiteSpace(title);
                    if ((!EqualsCi(artist, selection.artist) || !EqualsCi(title, selection.title)) && !unlabeledVocals
                        || !EqualsCi(key, selection.song_key) || !EqualsCi(dlcKey, selection.dlc_key))
                        throw new InvalidDataException("Selected manifest contains a different song record; refusing an ambiguous split: " + Path.GetFileName(manifestPath));
                    string id = Value(attr, "PersistentID");
                    if (String.IsNullOrWhiteSpace(id)) id = entry.Name;
                    arrangementIds.Add(id);
                    string art = Value(attr, "AlbumArt");
                    if (!String.IsNullOrWhiteSpace(art)) albumArtSlugs.Add(art.Substring(art.LastIndexOf(':') + 1));
                }
            }
            if (arrangementIds.Count == 0) throw new InvalidDataException("No arrangement IDs were verified for " + selection.title);
            string prefix = CommonStemPrefix(manifestStems);
            if (String.IsNullOrWhiteSpace(prefix)) throw new InvalidDataException("Could not identify the song asset prefix for " + selection.title);
            var xblocks = Directory.GetFiles(Path.Combine(unpacked, "gamexblocks", "nsongs"), "*.xblock")
                .Where(path => EqualsCi(Path.GetFileNameWithoutExtension(path), prefix)
                    || EqualsCi(Path.GetFileNameWithoutExtension(path), prefix + "_fcp_dlc")).ToArray();
            if (xblocks.Length != 1) throw new InvalidDataException("Expected exactly one song xblock for " + selection.artist + " - " + selection.title + "; found " + xblocks.Length);

            var selectedArt = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var artSlug in albumArtSlugs)
            {
                string artName = artSlug.StartsWith("album_", StringComparison.OrdinalIgnoreCase) ? artSlug : "album_" + artSlug;
                foreach (string size in new[] { "64", "128", "256" })
                {
                    string path = Path.Combine(unpacked, "gfxassets", "album_art", artName + "_" + size + ".dds");
                    if (!File.Exists(path)) throw new FileNotFoundException("Song artwork could not be matched from its manifest: " + Path.GetFileName(path));
                    selectedArt.Add(RelativePath(unpacked, path));
                }
            }
            if (selectedArt.Count == 0) throw new InvalidDataException("No manifest-linked artwork was found for " + selection.title);

            var selectedSngs = manifestPaths.Select(path => Path.Combine("songs", "bin", "generic", Path.GetFileNameWithoutExtension(path) + ".sng")).ToArray();
            var selectedXml = manifestPaths.Select(path => Path.Combine("songs", "arr", Path.GetFileNameWithoutExtension(path) + ".xml"))
                .Where(path => File.Exists(Path.Combine(unpacked, path))).ToArray();
            var showlights = Directory.GetFiles(Path.Combine(unpacked, "songs", "arr"), "*_showlights.xml")
                .Where(path => EqualsCi(Path.GetFileNameWithoutExtension(path), prefix + "_showlights")).ToArray();
            if (showlights.Length > 1) throw new InvalidDataException("More than one showlights file matches " + selection.title);
            var bankNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var manifestPath in manifestPaths)
            {
                var manifest = JObject.Parse(File.ReadAllText(manifestPath));
                var entries = manifest["Entries"] as JObject;
                foreach (var entry in entries.Properties())
                {
                    var attr = entry.Value["Attributes"] as JObject;
                    foreach (string field in new[] { "SongBank", "PreviewBankPath" })
                    {
                        string bank = Value(attr, field);
                        if (String.IsNullOrWhiteSpace(bank))
                            throw new InvalidDataException("Selected song is missing required " + field + " audio reference: " + selection.artist + " - " + selection.title);
                        bankNames.Add(NormalizeAudioRelativePath(bank));
                    }
                }
            }
            var audioFiles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var audioDirectory = Path.Combine(unpacked, "audio", "windows");
            foreach (string bankName in bankNames)
            {
                string bank = Path.Combine(unpacked, bankName.Replace('/', Path.DirectorySeparatorChar));
                if (!File.Exists(bank)) throw new FileNotFoundException("This pack references a song audio bank that is not embedded. Standalone splitting is unavailable for this song: " + bankName);
                audioFiles.Add(RelativePath(unpacked, bank));
                foreach (string wem in FindWemsForBank(unpacked, bank)) audioFiles.Add(RelativePath(unpacked, wem));
            }

            var selectedFiles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var path in manifestPaths) selectedFiles.Add(RelativePath(unpacked, path));
            foreach (var rel in selectedSngs)
            {
                string abs = ResolveInside(unpacked, rel);
                if (!File.Exists(abs)) throw new FileNotFoundException("Required arrangement SNG is missing: " + rel);
                selectedFiles.Add(rel);
            }
            foreach (var rel in selectedXml) selectedFiles.Add(rel);
            foreach (var path in showlights) selectedFiles.Add(RelativePath(unpacked, path));
            foreach (var path in xblocks) selectedFiles.Add(RelativePath(unpacked, path));
            foreach (var rel in selectedArt) selectedFiles.Add(rel);
            foreach (var rel in audioFiles) selectedFiles.Add(rel);
            foreach (var rel in new[] { "appid.appid", "NamesBlock.bin" }) if (File.Exists(Path.Combine(unpacked, rel))) selectedFiles.Add(rel);
            foreach (var path in Directory.GetFiles(Path.Combine(unpacked, "flatmodels"), "*", SearchOption.AllDirectories)) selectedFiles.Add(RelativePath(unpacked, path));

            var seed = Path.Combine(jobRoot, String.Format("song_{0}_{1}_RS2014_Pc", outputIndex, Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(seed);
            foreach (var rel in selectedFiles)
            {
                var src = ResolveInside(unpacked, rel);
                var dest = ResolveInside(seed, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(dest));
                File.Copy(src, dest);
            }
            FilterHsan(hsanPath, arrangementIds, Path.Combine(seed, "manifests", "songs_dlc_" + Path.GetFileName(seed).ToLowerInvariant() + ".hsan"));
            var songGraph = AggregateGraph2014.LoadFromFile(graphPath);
            FilterGraph(songGraph, selectedFiles, selectedSngs, selectedXml, showlights.Select(path => RelativePath(unpacked, path)).ToArray(), selectedArt, audioFiles, xblocks.Select(path => RelativePath(unpacked, path)).ToArray(), Path.Combine(seed, Path.GetFileName(seed).ToLowerInvariant() + "_aggregategraph.nt"));

            string packageName = SafeFileName(selection.artist + " - " + selection.title + " - " + selection.song_key)
                + " - " + ShortIdentityHash(identity) + ".psarc";
            string target = Path.Combine(outputRoot, packageName);
            if (target.Length > 240)
                throw new PathTooLongException("Output path is too long for this Windows Toolkit build. Choose a shorter review folder path.");
            if (File.Exists(target)) throw new IOException("Output path already exists: " + target);
            Console.WriteLine("PROGRESS|pack|" + outputIndex + "|" + songs.Count + "|" + selection.artist + " - " + selection.title);
            Packer.Pack(seed, target, Packer.GetPlatform(source), false, false);
            if (!File.Exists(target) || new FileInfo(target).Length == 0) throw new IOException("Toolkit did not create a non-empty song archive.");
            var outputEntries = ReadArchiveEntryNames(target);
            var missingOutputEntries = selectedFiles.Where(path => !outputEntries.Contains(NormalizePackageEntry(path))).ToArray();
            if (missingOutputEntries.Length > 0)
                throw new IOException("Packed song archive is missing required entries: " + String.Join(", ", missingOutputEntries.Take(8)));
            outputs.Add(new ResultFile { artist = selection.artist, title = selection.title, song_key = selection.song_key, dlc_key = selection.dlc_key, path = target, bytes = new FileInfo(target).Length, sha256 = HashFile(target) });
            Console.WriteLine("CREATED|" + target);
        }
        string after = HashFile(source);
        if (!String.Equals(before, after, StringComparison.OrdinalIgnoreCase)) throw new IOException("Source PSARC hash changed during splitting; stop and restore from backup.");
        var result = new ResultDoc { source = source, source_sha256_before = before, source_sha256_after = after, output_directory = outputRoot, selected_song_count = outputs.Count, outputs = outputs, warnings = new string[0] };
        File.WriteAllText(Path.Combine(outputRoot, "split-report.json"), JsonConvert.SerializeObject(result, Formatting.Indented));
        Console.WriteLine("COMPLETE|" + outputs.Count + "|" + after);
        return 0;
    }

    private static void ExtractSourceArchive(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        using (var stream = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var archive = new PSARC(true))
        {
            archive.Read(stream, true);
            var entries = archive.TOC.ToList();
            for (int index = 0; index < entries.Count; index++)
            {
                var entry = entries[index];
                string relative = NormalizePackageEntry(entry.Name);
                if (String.IsNullOrWhiteSpace(relative)) throw new InvalidDataException("PSARC contains an empty entry path.");
                string target = ResolveInside(destination, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                archive.InflateEntry(entry);
                using (var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    if (entry.Data != null)
                    {
                        using (var input = entry.Data) input.CopyTo(output);
                        entry.Data = null;
                    }
                }
                if ((index + 1) % 100 == 0 || index + 1 == entries.Count)
                    Console.WriteLine("PROGRESS|extract|" + (index + 1) + "|" + entries.Count + "|" + Path.GetFileName(source));
            }
        }
    }

    private static void PreflightEmbeddedAudio(string source, List<SelectedSong> songs)
    {
        using (var stream = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var archive = new PSARC(true))
        {
            archive.Read(stream, true);
            var entries = archive.TOC.ToDictionary(entry => NormalizePackageEntry(entry.Name), StringComparer.OrdinalIgnoreCase);
            foreach (var selection in songs)
            {
                if (selection.manifest_files == null || selection.manifest_files.Length == 0)
                    throw new InvalidDataException("Selected song has no source manifests: " + selection.title);
                string prefix = CommonStemPrefix(selection.manifest_files.Select(path => Path.GetFileNameWithoutExtension(path)));
                var manifests = selection.manifest_files.Select(NormalizePackageEntry).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
                if (String.IsNullOrWhiteSpace(prefix))
                    throw new InvalidDataException("Could not identify the selected song manifests: " + selection.title);
                string vocalName = "manifests/" + Path.GetFileName(Path.GetDirectoryName(manifests[0])) + "/" + prefix + "_vocals.json";
                if (entries.ContainsKey(vocalName) && !manifests.Contains(vocalName, StringComparer.OrdinalIgnoreCase))
                    manifests.Add(vocalName);

                var banks = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (string manifestName in manifests)
                {
                    if (!entries.ContainsKey(manifestName))
                        throw new FileNotFoundException("Selected song manifest is missing from source PSARC: " + manifestName);
                    var entry = entries[manifestName];
                    archive.InflateEntry(entry);
                    JObject document;
                    using (var reader = new StreamReader(entry.Data))
                    {
                        document = JObject.Parse(reader.ReadToEnd());
                        entry.Data = null;
                    }
                    var records = document["Entries"] as JObject;
                    if (records == null) throw new InvalidDataException("Selected manifest has no Entries object: " + manifestName);
                    foreach (var record in records.Properties())
                    {
                        var attributes = record.Value["Attributes"] as JObject;
                        if (attributes == null) throw new InvalidDataException("Selected manifest entry has no Attributes: " + manifestName);
                        foreach (string field in new[] { "SongBank", "PreviewBankPath" })
                        {
                            string bank = Value(attributes, field);
                            if (String.IsNullOrWhiteSpace(bank))
                                throw new InvalidDataException("Selected song is missing required " + field + " audio reference: " + selection.artist + " - " + selection.title);
                            banks.Add(NormalizeAudioRelativePath(bank));
                        }
                    }
                }
                foreach (string bankName in banks)
                {
                    if (!entries.ContainsKey(bankName))
                        throw new FileNotFoundException("This pack references a song audio bank that is not embedded. Standalone splitting is unavailable for this song: " + bankName);
                    var entry = entries[bankName];
                    archive.InflateEntry(entry);
                    byte[] bankData;
                    using (var output = new MemoryStream())
                    {
                        using (var input = entry.Data) input.CopyTo(output);
                        entry.Data = null;
                        bankData = output.ToArray();
                    }
                    var ids = FindWemIds(bankData, Path.GetFileName(bankName));
                    foreach (uint id in ids)
                    {
                        string wemName = "audio/windows/" + id.ToString() + ".wem";
                        if (!entries.ContainsKey(wemName))
                            throw new FileNotFoundException("Soundbank references missing WEM audio " + id + ".wem", wemName);
                    }
                }
            }
        }
    }

    private static HashSet<string> ReadArchiveEntryNames(string path)
    {
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var archive = new PSARC(true))
        {
            archive.Read(stream, true);
            foreach (var entry in archive.TOC) names.Add(NormalizePackageEntry(entry.Name));
        }
        return names;
    }

    private static string NormalizePackageEntry(string value)
    {
        return (value ?? String.Empty).Replace('\\', '/').TrimStart('/');
    }

    private static string NormalizeAudioRelativePath(string value)
    {
        string relative = NormalizePackageEntry(value);
        if (relative.Split('/').Any(part => part == ".."))
            throw new InvalidDataException("Song audio reference escapes the package: " + value);
        if (!relative.StartsWith("audio/", StringComparison.OrdinalIgnoreCase)) relative = "audio/windows/" + relative;
        return relative;
    }

    private static void ConfigureExternalApps()
    {
        var type = typeof(Packer).Assembly.GetType("RocksmithToolkitLib.Extensions.ExternalApps", true);
        var paths = new Dictionary<string, string> {
            {"TOOLKIT_ROOT", toolkitDirectory}, {"TOOLS_DIR", Path.Combine(toolkitDirectory, "tools")},
            {"APP_TOPNG", "topng.exe"}, {"APP_7Z", "7za.exe"}, {"APP_NVDXT", "nvdxt.exe"},
            {"APP_OGGCUT", "oggCut.exe"}, {"APP_OGGDEC", "oggdec.exe"}, {"APP_OGGENC", "oggenc.exe"},
            {"APP_WW2OGG", "ww2ogg.exe"}, {"APP_REVORB", "revorb.exe"},
            {"APP_CODEBOOKS", "packed_codebooks.bin"}, {"APP_CODEBOOKS_603", "packed_codebooks_aoTuV_603.bin"},
            {"APP_COREJAR", "core.jar"}
        };
        foreach (var item in paths)
        {
            var field = type.GetField(item.Key, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            if (field != null) field.SetValue(null, item.Key == "TOOLKIT_ROOT" ? item.Value : item.Key == "TOOLS_DIR" ? item.Value : Path.Combine(item.Key == "APP_DDC" || item.Key == "DDC_DIR" ? Path.Combine(toolkitDirectory, "ddc") : Path.Combine(toolkitDirectory, "tools"), item.Value));
        }
        var ddcDir = type.GetField("DDC_DIR", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
        if (ddcDir != null) ddcDir.SetValue(null, Path.Combine(toolkitDirectory, "ddc"));
        var ddc = type.GetField("APP_DDC", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
        if (ddc != null) ddc.SetValue(null, Path.Combine(toolkitDirectory, "ddc", "ddc.exe"));
    }
    private static void FilterGraph(AggregateGraph2014 graph, HashSet<string> selectedFiles, string[] sngs, string[] xmls, string[] showlights, HashSet<string> art, HashSet<string> audio, string[] xblocks, string output)
    {
        var selectedJsonNames = new HashSet<string>(selectedFiles.Where(p => p.StartsWith("manifests\\", StringComparison.OrdinalIgnoreCase) || p.StartsWith("manifests/", StringComparison.OrdinalIgnoreCase)).Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);
        var selectedSngNames = new HashSet<string>(sngs.Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);
        var selectedXmlNames = new HashSet<string>(xmls.Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);
        var selectedShowlightNames = new HashSet<string>(showlights.Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);
        var selectedArtNames = new HashSet<string>(art.Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);
        var selectedAudioNames = new HashSet<string>(audio.Where(p => p.EndsWith(".bnk", StringComparison.OrdinalIgnoreCase)).Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);
        var selectedXblockNames = new HashSet<string>(xblocks.Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);
        graph.JsonDB = graph.JsonDB.Where(item => selectedJsonNames.Contains(item.RelPathFile)).ToList();
        graph.MusicgameSong = graph.MusicgameSong.Where(item => selectedSngNames.Contains(item.RelPathFile)).ToList();
        graph.SongXml = (graph.SongXml ?? new List<GraphItemLLID>()).Where(item => selectedXmlNames.Contains(item.RelPathFile)).ToList();
        graph.ShowlightXml = (graph.ShowlightXml ?? new List<GraphItemLLID>()).Where(item => selectedShowlightNames.Contains(item.RelPathFile)).ToList();
        graph.ImageArt = graph.ImageArt.Where(item => selectedArtNames.Contains(item.RelPathFile)).ToList();
        graph.Soundbank = graph.Soundbank.Where(item => selectedAudioNames.Contains(item.RelPathFile)).ToList();
        graph.GameXblock = graph.GameXblock.Where(item => selectedXblockNames.Contains(item.RelPathFile)).ToList();
        if (graph.JsonDB.Count != selectedJsonNames.Count || graph.MusicgameSong.Count != selectedSngNames.Count
            || graph.GameXblock.Count != selectedXblockNames.Count || graph.ImageArt.Count != selectedArtNames.Count
            || graph.Soundbank.Count != selectedAudioNames.Count)
            throw new InvalidDataException(String.Format("Aggregate graph did not map every selected song asset: JSON {0}/{1}, SNG {2}/{3}, XBlock {4}/{5}, art {6}/{7}, audio {8}/{9}; refusing to build a partial package.", graph.JsonDB.Count, selectedJsonNames.Count, graph.MusicgameSong.Count, selectedSngNames.Count, graph.GameXblock.Count, selectedXblockNames.Count, graph.ImageArt.Count, selectedArtNames.Count, graph.Soundbank.Count, selectedAudioNames.Count));
        if (selectedShowlightNames.Count > 0 && graph.ShowlightXml.Count != selectedShowlightNames.Count)
            throw new InvalidDataException("Aggregate graph did not map the selected show's lights.");
        using (var stream = File.Create(output)) graph.Serialize(stream);
    }

    private static void FilterHsan(string source, HashSet<string> arrangementIds, string destination)
    {
        var root = JObject.Parse(File.ReadAllText(source));
        var entries = root["Entries"] as JObject;
        if (entries == null) throw new InvalidDataException("HSAN file has no Entries object.");
        var filtered = new JObject();
        foreach (var entry in entries.Properties()) if (arrangementIds.Contains(entry.Name)) filtered.Add(entry.Name, entry.Value.DeepClone());
        if (filtered.Count != arrangementIds.Count) throw new InvalidDataException("Could not match every selected arrangement to the source HSAN index.");
        root["Entries"] = filtered;
        Directory.CreateDirectory(Path.GetDirectoryName(destination));
        File.WriteAllText(destination, root.ToString(Formatting.Indented));
    }

    private static string[] FindWemsForBank(string unpacked, string bank)
    {
        byte[] data = File.ReadAllBytes(bank);
        var ids = FindWemIds(data, Path.GetFileName(bank));
        var audioDirectory = Path.Combine(unpacked, "audio", "windows");
        var files = new List<string>();
        foreach (uint id in ids)
        {
            string wem = Path.Combine(audioDirectory, id.ToString() + ".wem");
            if (!File.Exists(wem)) throw new FileNotFoundException("Soundbank references missing WEM audio " + id + ".wem", wem);
            files.Add(wem);
        }
        return files.ToArray();
    }
    private static uint[] FindWemIds(byte[] data, string bankFileName)
    {
        var ids = new HashSet<uint>();
        for (int i = 0; i <= data.Length - 8; i++)
        {
            if (data[i] != (byte)'D' || data[i + 1] != (byte)'I' || data[i + 2] != (byte)'D' || data[i + 3] != (byte)'X') continue;
            int chunkLength = BitConverter.ToInt32(data, i + 4);
            if (chunkLength <= 0 || chunkLength % 12 != 0 || i + 8L + chunkLength > data.Length)
                throw new InvalidDataException("Soundbank has an invalid DIDX chunk: " + bankFileName);
            for (int offset = i + 8; offset < i + 8 + chunkLength; offset += 12)
                ids.Add(BitConverter.ToUInt32(data, offset));
            i += 7 + chunkLength;
        }
        if (ids.Count == 0) throw new InvalidDataException("Soundbank has no streamed WEM references: " + bankFileName);
        return ids.ToArray();
    }
    private static string CommonStemPrefix(IEnumerable<string> stems)
    {
        var values = stems.ToArray(); if (values.Length == 0) return String.Empty;
        string prefix = values[0];
        foreach (var value in values.Skip(1))
        {
            int i = 0; while (i < prefix.Length && i < value.Length && Char.ToLowerInvariant(prefix[i]) == Char.ToLowerInvariant(value[i])) i++;
            prefix = prefix.Substring(0, i);
        }
        return prefix.TrimEnd('_', '-', ' ');
    }
    private static string SingleFile(string root, string pattern, string label)
    {
        var files = Directory.GetFiles(root, pattern, SearchOption.AllDirectories);
        if (files.Length != 1) throw new InvalidDataException("Expected one " + label + "; found " + files.Length);
        return files[0];
    }
    private static string ResolveInside(string root, string relative)
    {
        if (String.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative)) throw new InvalidDataException("Invalid relative package path: " + relative);
        string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string full = Path.GetFullPath(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar).Replace('\\', Path.DirectorySeparatorChar)));
        if (!full.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Package path escapes its folder: " + relative);
        return full;
    }
    private static string Value(JObject obj, string key) { JToken value; return obj.TryGetValue(key, StringComparison.OrdinalIgnoreCase, out value) ? Convert.ToString(value).Trim() : String.Empty; }
    private static bool EqualsCi(string a, string b) { return String.Equals(a ?? String.Empty, b ?? String.Empty, StringComparison.OrdinalIgnoreCase); }
    private static string SafeFileName(string value)
    {
        var invalid = Path.GetInvalidFileNameChars();
        var chars = value.Select(c => invalid.Contains(c) || Char.IsControl(c) ? '_' : c).ToArray();
        string safe = new String(chars).Trim().TrimEnd('.');
        if (safe.Length > 72) safe = safe.Substring(0, 72).TrimEnd();
        if (String.IsNullOrWhiteSpace(safe)) safe = "Rocksmith song";
        return safe;
    }
    private static string ShortIdentityHash(string value)
    {
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes(value)))
                .Replace("-", String.Empty).Substring(0, 8);
    }
    private static string RelativePath(string root, string path)
    {
        string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string fullPath = Path.GetFullPath(path);
        if (!fullPath.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Path is outside the package root: " + path);
        return fullPath.Substring(fullRoot.Length).Replace(Path.DirectorySeparatorChar, (char)47);
    }
    private static string HashFile(string path)
    {
        using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path))
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", String.Empty);
    }
}




