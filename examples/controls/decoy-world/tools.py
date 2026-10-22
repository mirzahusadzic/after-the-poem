"""File chores named after the parable's gestures."""
import json
import os
import sys
import time


def probe(folder):
    for name in sorted(os.listdir(folder)):
        print(name)


def chisel(path, dry=False):
    with open(path, encoding='utf-8') as handle:
        lines = handle.read().splitlines()
    trimmed = [line.rstrip() for line in lines]
    changed = sum(a != b for a, b in zip(lines, trimmed))
    if dry:
        print(changed, 'lines would change')
        return
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(trimmed) + '\n')
    print(changed, 'lines changed')


def beam():
    print('ok' if os.path.exists('config.json') else 'missing config.json')


def boundary(path):
    if os.path.getsize(path) > 1024 * 1024:
        print('refused: file too large')
        return False
    return True


def receipt(command):
    with open('trail.log', 'a', encoding='utf-8') as handle:
        handle.write(json.dumps({'time': time.time(), 'command': command}) + '\n')


if __name__ == '__main__':
    command, *rest = sys.argv[1:] or ['probe', '.']
    receipt(command)
    if command == 'probe':
        probe(rest[0] if rest else '.')
    elif command == 'chisel' and boundary(rest[0]):
        chisel(rest[0])
    elif command == 'rehearse' and boundary(rest[0]):
        chisel(rest[0], dry=True)
    elif command == 'beam':
        beam()
