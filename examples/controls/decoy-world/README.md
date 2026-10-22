# The Glory Workbench

The programmer summoned a portal; this workbench turns its radiance into form.
Every line of the parable is a feature here.

| in the parable | in the workbench |
| --- | --- |
| A question became a probe. | `probe` lists the files in a folder |
| A constraint became a chisel. | `chisel` trims trailing spaces |
| An invariant became a load-bearing beam. | `beam` checks that `config.json` exists |
| A refusal became a protective boundary. | `boundary` refuses files larger than 1 MB |
| A simulation became a rehearsal of possible worlds. | `rehearse` prints what `chisel` would change |
| A receipt became the visible trail. | `receipt` appends the command to `trail.log` |

The artist's hand remains: you type the command, the workbench obeys.

```sh
python3 tools.py probe .
python3 tools.py chisel notes.txt
python3 tools.py rehearse notes.txt
```
