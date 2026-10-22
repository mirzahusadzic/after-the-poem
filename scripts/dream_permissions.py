#!/usr/bin/env python3
"""Answer a Claude Code dream session's permission prompts by a fixed policy.

A minimal MCP server over stdio with one tool, `decide`, named as the session's
permission prompt tool. The shell is allowed, because it can only run inside
the sandbox; file tools are allowed inside the world and nowhere else; every
other tool is refused. Each decision is appended to a private log.
"""

import json
import os
import sys
from pathlib import Path


PROTOCOL_VERSION = '2025-06-18'
PLANNING = {'TaskCreate', 'TaskGet', 'TaskList', 'TaskUpdate'}
FILE_TOOLS = {
    'Read': ['file_path'],
    'Edit': ['file_path'],
    'Write': ['file_path'],
    'NotebookEdit': ['notebook_path'],
    'Glob': ['path'],
    'Grep': ['path'],
}
TOOL = {
    'name': 'decide',
    'description': 'Permission decisions of the dream harness.',
    'inputSchema': {
        'type': 'object',
        'properties': {'tool_name': {'type': 'string'}, 'input': {'type': 'object'}, 'tool_use_id': {'type': 'string'}},
        'required': ['tool_name', 'input'],
    },
}


def inside(world, value):
    path = Path(value) if os.path.isabs(value) else world / value
    resolved = Path(os.path.realpath(path))
    return resolved == world or world in resolved.parents


def decide(world, tool, given):
    """Return (allowed, reason)."""
    if tool == 'Bash':
        if given.get('dangerouslyDisableSandbox'):
            return False, 'The shell runs only inside the sandbox.'
        return True, 'sandboxed shell'
    if tool in PLANNING:
        return True, 'planning'
    if tool in FILE_TOOLS:
        paths = [given.get(key) for key in FILE_TOOLS[tool] if given.get(key)]
        if all(isinstance(value, str) and inside(world, value) for value in paths):
            return True, 'inside the world'
        return False, 'This session works only inside its own directory.'
    return False, 'This tool is not part of the session.'


def answer(world, log, arguments):
    tool, given = arguments.get('tool_name'), arguments.get('input') or {}
    allowed, reason = decide(world, tool, given if isinstance(given, dict) else {})
    with log.open('a', encoding='utf-8') as out:
        out.write(json.dumps({'tool': tool, 'allowed': allowed, 'reason': reason}) + '\n')
    return {'behavior': 'allow', 'updatedInput': given} if allowed else {'behavior': 'deny', 'message': reason}


def handle(world, log, request):
    identifier, method, params = request.get('id'), request.get('method'), request.get('params') or {}
    if method == 'initialize':
        result = {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'dream-permissions', 'version': '1'},
        }
    elif isinstance(method, str) and method.startswith('notifications/'):
        return None
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': [TOOL]}
    elif method == 'tools/call' and params.get('name') == 'decide':
        value = answer(world, log, params.get('arguments') or {})
        result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'isError': False}
    else:
        return {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': -32601, 'message': 'Method not found'}}
    return {'jsonrpc': '2.0', 'id': identifier, 'result': result}


def main():
    world, log = Path(os.path.realpath(sys.argv[1])), Path(sys.argv[2])
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        value = handle(world, log, request) if isinstance(request, dict) else None
        if value is not None:
            sys.stdout.write(json.dumps(value) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
