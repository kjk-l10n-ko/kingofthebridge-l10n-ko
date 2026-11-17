// Inserts a Korean lookup in front of SteamAchievements.TryGetNameOfAchievement /
// TryGetDescriptionOfAchievement. Unknown ids fall through to the original Steam text.
using System.Text.Json;
using MethodAttributes = Mono.Cecil.MethodAttributes;
using ParameterAttributes = Mono.Cecil.ParameterAttributes;
using TypeAttributes = Mono.Cecil.TypeAttributes;
using Mono.Cecil;
using Mono.Cecil.Cil;

if (args.Length != 3)
{
    Console.Error.WriteLine("usage: kingpatch <in.dll> <out.dll> <achievements.json>");
    return 2;
}
string inDll = args[0], outDll = args[1], jsonPath = args[2];

var rows = JsonSerializer.Deserialize<List<Row>>(File.ReadAllText(jsonPath))!
    .Where(r => !string.IsNullOrWhiteSpace(r.ko_name) && !string.IsNullOrWhiteSpace(r.ko_desc))
    .ToList();
if (rows.Count == 0) { Console.Error.WriteLine("no rows"); return 1; }

var rp = new ReaderParameters { InMemory = true, ReadWrite = false };
using var mod = ModuleDefinition.ReadModule(inDll, rp);

var type = mod.GetType("SteamAchievements")
    ?? throw new Exception("SteamAchievements type not found");
var nameGetter = type.Methods.FirstOrDefault(m => m.Name == "TryGetNameOfAchievement")
    ?? throw new Exception("TryGetNameOfAchievement not found");
var descGetter = type.Methods.FirstOrDefault(m => m.Name == "TryGetDescriptionOfAchievement")
    ?? throw new Exception("TryGetDescriptionOfAchievement not found");
if (type.Methods.Any(m => m.Name == "KonAchievementName"))
    throw new Exception("dll already patched (KonAchievementName exists); use a pristine input");

MethodDefinition MakeLookup(ModuleDefinition m, TypeDefinition host, string name,
                           IEnumerable<(string id, string ko)> entries)
{
    var str = m.TypeSystem.String;
    var opEq = new MethodReference("op_Equality", m.TypeSystem.Boolean, str) { HasThis = false };
    opEq.Parameters.Add(new ParameterDefinition(str));
    opEq.Parameters.Add(new ParameterDefinition(str));

    var md = new MethodDefinition(name,
        MethodAttributes.Private | MethodAttributes.Static | MethodAttributes.HideBySig, str);
    md.Parameters.Add(new ParameterDefinition("id", ParameterAttributes.None, str));
    var il = md.Body.GetILProcessor();

    var list = entries.ToList();
    var starts = list.Select(_ => Instruction.Create(OpCodes.Ldarg_0)).ToArray();
    var miss = Instruction.Create(OpCodes.Ldnull);
    var ret = Instruction.Create(OpCodes.Ret);
    for (int i = 0; i < list.Count; i++)
    {
        il.Append(starts[i]);
        il.Append(Instruction.Create(OpCodes.Ldstr, list[i].id));
        il.Append(Instruction.Create(OpCodes.Call, opEq));
        il.Append(Instruction.Create(OpCodes.Brfalse, i + 1 < list.Count ? starts[i + 1] : miss));
        il.Append(Instruction.Create(OpCodes.Ldstr, list[i].ko));
        il.Append(Instruction.Create(OpCodes.Ret));
    }
    il.Append(miss);
    il.Append(ret);
    md.Body.MaxStackSize = 2;
    host.Methods.Add(md);
    return md;
}

var koName = MakeLookup(mod, type, "KonAchievementName", rows.Select(r => (r.id, r.ko_name)));
var koDesc = MakeLookup(mod, type, "KonAchievementDesc", rows.Select(r => (r.id, r.ko_desc)));

void Prepend(MethodDefinition target, MethodDefinition helper)
{
    var body = target.Body;
    if (body.Instructions[^1].OpCode != OpCodes.Ret)
        throw new Exception($"{target.Name}: last instruction is not ret");
    var il = body.GetILProcessor();
    var first = body.Instructions[0];
    // if (Kon...(id) is string ko) return ko;
    var ret = il.Create(OpCodes.Ret);
    il.InsertBefore(first, il.Create(OpCodes.Ldarg_0));
    il.InsertBefore(first, il.Create(OpCodes.Call, helper));
    il.InsertBefore(first, il.Create(OpCodes.Dup));
    il.InsertBefore(first, il.Create(OpCodes.Brtrue, ret));
    il.InsertBefore(first, il.Create(OpCodes.Pop));
    il.Append(ret);
}

Prepend(nameGetter, koName);
Prepend(descGetter, koDesc);

mod.Write(outDll, new WriterParameters { WriteSymbols = false });

// 1) reload: metadata round-trips and the expected members/IL are there
using (var check = ModuleDefinition.ReadModule(outDll, rp))
{
    var ct = check.GetType("SteamAchievements") ?? throw new Exception("reload: type missing");
    foreach (var m in new[] { "KonAchievementName", "KonAchievementDesc", "TryGetNameOfAchievement", "TryGetDescriptionOfAchievement" })
        if (ct.Methods.All(x => x.Name != m)) throw new Exception($"reload: {m} missing");
    var kn = ct.Methods.First(m => m.Name == "KonAchievementName");
    int expected = rows.Count * 6 + 2;
    if (kn.Body.Instructions.Count != expected)
        throw new Exception($"reload: {kn.Name} has {kn.Body.Instructions.Count} instrs, expected {expected}");
    foreach (var mn in new[] { "TryGetNameOfAchievement", "TryGetDescriptionOfAchievement" })
    {
        var pre = ct.Methods.First(m => m.Name == mn).Body.Instructions;
        if (pre[0].OpCode != OpCodes.Ldarg_0 || pre[1].OpCode != OpCodes.Call ||
            pre[2].OpCode != OpCodes.Dup || pre[3].OpCode != OpCodes.Brtrue || pre[4].OpCode != OpCodes.Pop)
            throw new Exception($"reload: {mn} prelude not in place");
        var body = ct.Methods.First(m => m.Name == mn).Body;
        if (body.ExceptionHandlers.Count != 1) throw new Exception($"reload: {mn} handler count");
        var h = body.ExceptionHandlers[0];
        if (h.TryStart == null || h.TryEnd == null || h.HandlerStart == null)
            throw new Exception($"reload: {mn} dangling handler range");
        if (pre[5] != h.TryStart)
            throw new Exception($"reload: {mn} try block did not shift behind the prelude");
    }
}

// 2) the generated IL itself must JIT and behave: build the same lookups in a scratch
//    assembly and execute them (verifies op_Equality binding, branches, utf-16 strings)
{
    var asm = AssemblyDefinition.CreateAssembly(
        new AssemblyNameDefinition("Konselftest", new Version(1, 0, 0, 0)), "Konselftest", ModuleKind.Dll);
    var host = new TypeDefinition("", "T",
        TypeAttributes.Public | TypeAttributes.Abstract | TypeAttributes.Sealed, asm.MainModule.TypeSystem.Object);
    asm.MainModule.Types.Add(host);
    MakeLookup(asm.MainModule, host, "N", rows.Select(r => (r.id, r.ko_name)));
    MakeLookup(asm.MainModule, host, "D", rows.Select(r => (r.id, r.ko_desc)));
    string tmp = Path.Combine(Path.GetTempPath(), "konselftest.dll");
    asm.Write(tmp);
    try
    {
        var t = System.Reflection.Assembly.LoadFile(tmp).GetType("T") ?? throw new Exception("selftest: type T missing");
        var flags = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Public |
                    System.Reflection.BindingFlags.Static;
        var fn = t.GetMethod("N", flags)!;
        var fd = t.GetMethod("D", flags)!;
        foreach (var r in rows)
        {
            if ((string?)fn.Invoke(null, new object[] { r.id }) != r.ko_name) throw new Exception($"selftest: name {r.id}");
            if ((string?)fd.Invoke(null, new object[] { r.id }) != r.ko_desc) throw new Exception($"selftest: desc {r.id}");
        }
        if (fn.Invoke(null, new object[] { "NO_SUCH_ACHIEVEMENT" }) != null) throw new Exception("selftest: miss path");
    }
    finally { File.Delete(tmp); }
}

Console.WriteLine($"patched {rows.Count} achievements -> {outDll}");
return 0;

record Row(string id, string en_name, string en_desc, string ko_name, string ko_desc);
