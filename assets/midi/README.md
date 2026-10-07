# Retrolarium SMB demo music

`smb-ground-demo.mid` is an original four-voice loop written for Retrolarium's
Super Mario Bros. authoring preset. It deliberately uses only MIDI channels 1,
2, 3 and 10, corresponding to the two NES pulse voices, triangle and noise.

The composition and MIDI file are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). Regenerate the
file with:

```powershell
$env:PYTHONPATH = "retrolarium_kit/src"
python retrolarium_kit/tools/generate_smb_demo_midi.py `
  retrolarium_kit/assets/midi/smb-ground-demo.mid
```

To install the loop into an extracted SMB1 resource document and route every
ground-music schedule entry to it, run:

```powershell
$env:PYTHONPATH = "retrolarium_kit/src"
python retrolarium_kit/tools/apply_smb_ground_demo.py `
  path/to/retrolarium/documents/resources.json
```
