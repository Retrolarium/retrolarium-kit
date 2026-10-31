"""Record at native input consumption with skipped rendering; replay without skips.

Uses an isolated headless MAME, synthetic controller fields and output-only ROM
copies. The supplied project's documents are read, never edited. Checks the
weapon setup and more than 30 seconds of gameplay, including RNG/player state.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import argparse

KIT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(KIT / "src"))


def main():
    from retrolarium_kit.games.alien_soldier import playtest, workspace, demos
    from retrolarium_kit.platform.megadrive.build import stamp

    parser = argparse.ArgumentParser(
        description="Headless demo record/replay determinism oracle; needs a prepared source project built once."
    )
    parser.add_argument("project", type=Path)
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = args.project.resolve().parent / "alien-soldier-source"
    record = json.loads((snapshot / "retrolarium-source-snapshot.json").read_text())
    revision = record["revision"]
    rom = (snapshot.parent / "build/alien-soldier-source.bin").read_bytes()
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), revision)
    doc = demos.export(tree)
    words = [(s["held"] << 8) | s["pressed"] for s in doc["demos"][0]["steps"] for _ in range(s["frames"])]
    addresses = {
        n: tree.equates[n] & 0xFFFFFF
        for n in [
            "GameModeIndex",
            "GameSubstateIndex",
            "FrameCounter",
            "RandomNumberState",
            "PlayerXPosition",
            "PlayerYPosition",
            "PlayerHealth",
        ]
    }
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.update(playtest.demo_environment(snapshot, revision, 0))
    env["ALIEN_DEMO_RECORD"] = str(folder / "recording.txt")
    demo_script = KIT / "src/retrolarium_kit/studio/runtime/alien_soldier/demo.lua"
    lua = """local m=manager.machine
    local mem=m.devices[":maincpu"].spaces["program"]
    local fields={}
    for tag,port in pairs(m.ioport.ports) do
     if tag:match("^:ctrl1:") then for _,f in pairs(port.fields) do fields[#fields+1]=f end end
    end
    local function held(value)
     for _,f in ipairs(fields) do f:set_value((value & f.mask) ~= 0 and 1 or 0) end
    end
    local words=WORDS
    local addresses=ADDRESSES
    local recording=os.getenv("ALIEN_DEMO_RECORD") ~= nil
    local trace=assert(io.open(TRACE,"w"))
    dofile(SCRIPT)
    local count=0
    oracle_tap=mem:install_write_tap(INPUT,INPUT+1,"oracle",function(_,data,mask)
     if mask~=0xFFFF or mem:read_u16(ACTIVE)==0 then return data end
     count=count+1
     local row={tostring(count)}
     for _,at in ipairs(addresses) do row[#row+1]=tostring(mem:read_u32(at)) end
     trace:write(table.concat(row," "),"\\n")
     if recording then
      local mode=mem:read_u16(MODE)
      if mode==0x70 or mode==0x74 then held((words[count+1] or 0)>>8)
      elseif mode==0x10 then
       local phase=count%180
       held((phase<90 and 8 or 4) | (phase%40<20 and 0x40 or 0))
      end
     end
     if count>=2500 then trace:close();m:exit() end
     return data
    end)
    emu.register_frame_done(function() m.video.throttled=false end)
    """
    lua = lua.replace("WORDS", "{" + ",".join(map(str, words)) + "}")
    lua = lua.replace("ADDRESSES", "{" + ",".join(str(a) for a in addresses.values()) + "}")
    lua = lua.replace("SCRIPT", json.dumps(str(demo_script).replace("\\", "/")))
    lua = lua.replace("INPUT", str(int(env["ALIEN_RAM_DemoPlaybackInputWord"], 16)))
    lua = lua.replace("MODE", str(addresses["GameModeIndex"]))
    lua = lua.replace("ACTIVE", str(int(env["ALIEN_RAM_DemoPlaybackActive"], 16)))
    mame = args.mame.resolve()

    def run(kind, image, environment, skip):
        script = folder / f"{kind}.lua"
        script.write_text(
            lua.replace("TRACE", json.dumps(str(folder / f"{kind}.trace").replace("\\", "/"))), encoding="utf-8"
        )
        cartridge = folder / f"{kind}.bin"
        cartridge.write_bytes(image)
        args = [
            str(mame),
            "megadrij" if revision == "jp" else "megadriv",
            "-cart",
            str(cartridge),
            "-video",
            "none",
            "-sound",
            "none",
            "-nothrottle",
            "-noreadconfig",
            "-nowriteconfig",
            "-skip_gameinfo",
            "-seconds_to_run",
            "100",
            "-frameskip",
            str(skip),
            "-autoboot_delay",
            "0",
            "-autoboot_script",
            str(script),
        ]
        (folder / f"{kind}.trace").unlink(missing_ok=True)
        result = subprocess.run(
            args,
            cwd=folder,
            env=environment,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=180,
        )
        (folder / f"{kind}.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        assert result.returncode == 0, (kind, result.stdout[-1500:], result.stderr[-1500:])
        rows = (folder / f"{kind}.trace").read_text().splitlines()
        assert len(rows) == 2500, (kind, len(rows), result.stdout[-1000:])
        print(kind, len(rows), flush=True)
        assert any(int(row.split()[1]) >> 16 == 0x10 for row in rows), "The test did not reach gameplay"
        return rows

    recorded = run("record", rom, env, 10)
    prefix = int((folder / "recording.txt").read_text().splitlines()[0].split()[1])
    assert int(recorded[prefix].split()[1]) >> 16 == 0x70, "Recording did not hand over at weapon setup"
    assert sum(int(row.split()[1]) >> 16 == 0x10 for row in recorded) >= 1200, "Not enough gameplay ticks"
    steps = demos.recorded_steps(doc["demos"][0]["steps"], (folder / "recording.txt").read_text())
    doc["demos"][0]["steps"] = steps
    cache = json.loads((snapshot.parent / "build/alien-soldier-validation.json").read_text())
    patched, changed = demos.patch(rom, doc, cache["demo_layout"])
    settings = workspace.source_profile(snapshot)["revisions"][revision]
    if changed:
        patched = stamp(patched, *[int(v, 0) for v in settings["checksum"]])
    env.pop("ALIEN_DEMO_RECORD")
    replayed = run("play", patched, env, 0)
    for i, (a, b) in enumerate(zip(recorded, replayed)):
        assert a == b, (i + 1, a, b)
    print(
        "PASS: 2500 native input ticks identical through weapon setup and gameplay; record frameskip=10, replay=0",
        flush=True,
    )


if __name__ == "__main__":
    main()
