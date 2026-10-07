#!/usr/bin/env python3
"""Native workspace gestures; explicit adoption of simple Omarchy input rules."""
import json
import os
import platform
from pathlib import Path
import re
import stat
import sys
import subprocess
import importlib.util
import time
import trackpads as core

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'hypr'
INPUT = CONFIG / 'input.lua'
JOURNAL = core.DIRECTORY / 'gestures.pending.json'
BACKUP = core.DIRECTORY / 'gesture-input-original.lua.txt'
BEGIN = '-- BEGIN Trackpad Plus gestures\n'
END = '-- END Trackpad Plus gestures\n'
ANCHOR = '-- Trackpad Plus original gesture location\n'
PROVIDERS = ('hymission', 'trackpad-plus')
COMPANION = Path(__file__).resolve().with_name('overview-control.py')
# Fixed command text, never caller-provided Lua/shell. Expansion happens in the
# compositor's environment, preserving spaces and avoiding a machine-specific path.
COMPANION_COMMAND = 'python3 -B "${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/davefano.trackpad-plus/overview-control.py" '

# Tokens preserve positions while comments/strings can be masked for structural checks.
TOKEN = re.compile(r'--\[(=*)\[[\s\S]*?\]\1\]|--[^\n]*|\[(=*)\[[\s\S]*?\]\2\]|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
CALL = re.compile(r'hl\.gesture\s*\(\s*\{([^{}]*)\}\s*\)\s*;?')


def masked(source, strings=False):
    def replace(match):
        value = match.group()
        if value.startswith('--') or strings:
            return re.sub(r'[^\n]', ' ', value)
        return value
    return TOKEN.sub(replace, source)


def validate(settings):
    if not isinstance(settings, dict):
        raise ValueError('Expected workspace swipe settings')
    required = {'enabled', 'fingers', 'distance', 'invert'}
    optional = {'overview', 'overview_provider', 'fullscreen_up', 'scratchpad_down'}
    if required - set(settings) or set(settings) - (required | optional):
        raise ValueError('Expected workspace swipe settings')
    if type(settings['enabled']) is not bool or type(settings['invert']) is not bool:
        raise ValueError('Expected an on/off value')
    if type(settings.get('overview', False)) is not bool:
        raise ValueError('Expected an overview on/off value')
    for key in ('fullscreen_up', 'scratchpad_down'):
        if type(settings.get(key, False)) is not bool:
            raise ValueError('Expected an on/off value')
    if 'overview_provider' in settings and settings['overview_provider'] not in PROVIDERS:
        raise ValueError('Choose Trackpad Plus or HyMission for overview')
    if settings.get('overview', False) and (settings.get('fullscreen_up', False) or settings.get('scratchpad_down', False)):
        raise ValueError('Overview owns up and down swipes; turn it off for fullscreen or scratchpad')
    if (settings.get('fullscreen_up', False) or settings.get('scratchpad_down', False)) and not settings['enabled']:
        raise ValueError('Fullscreen and scratchpad swipes need workspace swipe enabled')
    if type(settings['fingers']) is not int or settings['fingers'] not in (3, 4):
        raise ValueError('Choose three or four fingers')
    if type(settings['distance']) is not int or not 50 <= settings['distance'] <= 2000:
        raise ValueError('Swipe distance must be between 50 and 2000')
    return settings


def normalized(settings):
    merged = dict(validate(settings))
    merged.setdefault('overview', False)
    merged.setdefault('fullscreen_up', False)
    merged.setdefault('scratchpad_down', False)
    return merged


def companion_status(operation='status'):
    """Capture-free inspection; only the explicit preview action may open it.

    Reuse the controller's bounded IPC and ownership validation in-process. This
    avoids nesting a startup process under the trackpad state lock.
    """
    if operation not in ('status', 'open'):
        raise ValueError('Unsupported overview operation')
    if not COMPANION.is_file():
        return dict(installed=False, reachable=False, protocolCompatible=False,
                    rendered=False, message='Install the complete Trackpad Plus plugin to use overview')
    try:
        spec = importlib.util.spec_from_file_location('trackpad_overview_control', COMPANION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        controller = module.Controller()
        result = controller.execute(operation)
        if operation == 'open':
            # Readiness is not rendering. Give this explicit test a bounded
            # chance to report a real frame; ordinary inspection never captures.
            deadline = time.monotonic() + 2.5
            while result.get('opened') and not result.get('rendered') and time.monotonic() < deadline:
                time.sleep(0.05)
                result = controller.execute('status')
        return result
    except Exception as exc:
        # Includes the controller's own ControlError; discovery must not disable
        # unrelated pointer or horizontal gesture controls.
        return dict(installed=True, reachable=False, protocolCompatible=False,
                    rendered=False, error=str(exc), message='Overview is unavailable: ' + str(exc))


def overview_provider(settings):
    # Schema 2/3 always meant HyMission; never silently migrate a live binding.
    return settings.get('overview_provider', 'hymission')


def hymission_status():
    # HyMission requires CFunctionHook::hook(), which Hyprland currently
    # implements only on x86_64. Loading the plugin/API alone is insufficient.
    machine = platform.machine().lower()
    supported = machine in ('x86_64', 'amd64')
    result = dict(available=False, supported=supported, version='', message='Install and load HyMission to enable workspace overview')
    if not supported:
        architecture = 'ARM64' if machine in ('aarch64', 'arm64') else machine or 'this architecture'
        result['message'] = 'HyMission overview is unavailable on %s: Hyprland function hooks require x86_64. Workspace swipes still work.' % architecture
        return result
    try:
        plugins = json.loads(core.hypr('plugin', 'list', '-j'))
        if not isinstance(plugins, list):
            raise ValueError('Invalid plugin list')
        plugin = next((p for p in plugins if isinstance(p, dict) and p.get('name') == 'hymission'), None)
        if plugin:
            core.hypr('eval', 'assert(hl.plugin.hymission and type(hl.plugin.hymission.gesture) == "function", "HyMission gesture API unavailable")')
            result.update(available=True, version=str(plugin.get('version', '')), message='HyMission loaded')
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result['message'] = 'Could not verify HyMission: ' + str(exc)
    return result


def runtime_settings():
    distance = json.loads(core.hypr('getoption', 'gestures:workspace_swipe_distance', '-j'))['int']
    invert = json.loads(core.hypr('getoption', 'gestures:workspace_swipe_invert', '-j'))['bool']
    return dict(distance=distance, invert=invert)


def parse_call(body):
    fields = {}
    for field in body.strip().strip(',').split(','):
        match = re.fullmatch(r'\s*([a-z_]+)\s*=\s*("[^"\\]*"|\'[^\'\\]*\'|[34])\s*', field)
        if not match or match[1] in fields:
            raise ValueError('Gesture uses expressions or options this editor cannot safely adopt')
        fields[match[1]] = match[2].strip('"\'')
    if set(fields) != {'fingers', 'direction', 'action'}:
        raise ValueError('Gesture uses options this editor cannot safely adopt')
    return fields


def find_binding(source):
    """Recognize simple top-level declarations, never evaluate user Lua."""
    code = masked(source)
    structure = masked(source, strings=True)
    if not re.search(r'\bgesture\b', structure):
        return None
    if re.search(r'\b(function|if|for|while|repeat|goto)\b', structure):
        raise ValueError('Conditional or function-based gestures must be managed in your Hyprland config')
    calls = [match for match in CALL.finditer(code)
             if structure[match.start():match.start() + len('hl.gesture')] == 'hl.gesture']
    remainder = list(structure)
    found = []
    previous_end = 0
    depths = dict.fromkeys("({[", 0)
    for match in calls:
        # A declaration nested in an expression is not safe to replace.
        prefix = structure[previous_end:match.start()]
        for opening, closing in [('(', ')'), ('{', '}'), ('[', ']')]:
            depths[opening] += prefix.count(opening) - prefix.count(closing)
        previous_end = match.end()
        if any(depths.values()):
            raise ValueError('Nested gestures must be managed in your Hyprland config')
        try:
            fields = parse_call(match[1])
        except ValueError:
            # Four-field bindings such as the manual scratchpad line carry a
            # workspace_name the adoption parser cannot represent. Keep them
            # like plain vertical lines: never adopt, never drop. Requesting
            # overview or Mac vertical swipes still refuses with a clear
            # conflict in change().
            if re.search(r'direction\s*=\s*["\'](vertical|up|down|swipe)', match[1]):
                fields = {'direction': 'vertical-exotic'}
            else:
                raise
        if fields['direction'] in ('horizontal', 'left', 'right', 'swipe'):
            if fields['direction'] != 'horizontal' or fields['action'] != 'workspace':
                raise ValueError('An existing horizontal gesture conflicts with workspace swiping')
            found.append((match.start(), match.end(), int(fields['fingers'])))
        for index in range(match.start(), match.end()): remainder[index] = ' '
    if re.search(r'\bgesture\b', ''.join(remainder)) or len(found) > 1:
        raise ValueError('Multiple or indirect gesture bindings need manual configuration')
    return found[0] if found else None


def block(settings, original, separator='', version=3):
    validate(settings)
    mac = bool(settings.get('fullscreen_up', False)) or bool(settings.get('scratchpad_down', False))
    if version == 8:
        settings = dict(normalized(settings), overview_provider=overview_provider(settings))
    elif mac:
        raise ValueError('Unsupported gesture block version')
    elif version in (4, 5, 6, 7):
        settings = dict(normalized(settings), overview_provider=overview_provider(settings))
        del settings['fullscreen_up']
        del settings['scratchpad_down']
    elif version == 3 and 'overview_provider' not in settings:
        settings = normalized(settings)
        del settings['fullscreen_up']
        del settings['scratchpad_down']
    elif version != 2 or 'overview' in settings or 'overview_provider' in settings:
        raise ValueError('Unsupported gesture block version')
    if original:
        binding = find_binding(original)
        if not binding or original[:binding[0]].strip() or original[binding[1]:].strip():
            raise ValueError('Invalid original gesture in managed settings')
    if separator not in ('', '\n'):
        raise ValueError('Invalid managed gesture separator')
    data = dict(version=version, settings=settings, original=original, separator=separator)
    lines = [BEGIN.rstrip(), '-- ' + json.dumps(data, sort_keys=True)]
    horizontal = 'hl.gesture({ fingers = %d, direction = "horizontal", action = "workspace" })' % settings['fingers']
    if settings.get('overview') and overview_provider(settings) == 'trackpad-plus':
        if version >= 7:
            lines.append('hl.layer_rule({ match = { namespace = "^trackpad-plus-overview$" }, no_anim = true, animation = "none" })')
        if settings['enabled']:
            lines.append(horizontal)
        for direction, operation in [('up', 'open'), ('down', 'close')]:
            command = json.dumps(COMPANION_COMMAND + operation)
            if version >= 6 and direction == 'up':
                # Recognition commits the opening; never wait for finger lift
                # or issue a second open at finish after the user dismisses it.
                action = '{ start = function() hl.exec_cmd(%s) end }' % command
            elif version >= 5:
                # Hyprland 0.56.2 starts the hidden companion while the finger is
                # moving; only a completed, non-cancelled gesture may show it.
                callbacks = []
                if direction == 'up':
                    callbacks.append('start = function() hl.exec_cmd(%s) end' % json.dumps(COMPANION_COMMAND + 'start'))
                callbacks.append('finish = function(event) if not event.cancelled then hl.exec_cmd(%s) end end' % command)
                action = '{ ' + ', '.join(callbacks) + ' }'
            else:
                # Canonical historical bytes are part of parse/restore validation.
                action = 'function() hl.exec_cmd(%s) end' % command
            lines.append('hl.gesture({ fingers = %d, direction = %s, action = %s })' %
                         (settings['fingers'], json.dumps(direction), action))
    elif settings.get('overview'):
        lines.append('if hl.plugin.hymission and hl.plugin.hymission.gesture then')
        if settings['enabled']:
            lines.append('  ' + horizontal.replace('hl.gesture', 'hl.plugin.hymission.gesture'))
        lines.append('  hl.plugin.hymission.gesture({ fingers = %d, direction = "vertical", action = "toggle", args = "forceall" })' % settings['fingers'])
        if settings['enabled']:
            lines += ['else', '  ' + horizontal]
        lines.append('end')
    elif settings.get('fullscreen_up') or settings.get('scratchpad_down'):
        lines.append(horizontal)
        if settings.get('fullscreen_up'):
            lines.append('hl.gesture({ fingers = %d, direction = "up", action = "fullscreen" })' % settings['fingers'])
        if settings.get('scratchpad_down'):
            lines.append('hl.gesture({ fingers = %d, direction = "down", action = "special", workspace_name = "scratchpad" })' % settings['fingers'])
    elif settings['enabled']:
        lines.append(horizontal)
    lines += ['hl.config({ gestures = { workspace_swipe_distance = %d, workspace_swipe_invert = %s } })' %
              (settings['distance'], str(settings['invert']).lower()), END.rstrip()]
    return '\n'.join(lines) + '\n'


def top_level_marker(source, position, marker, context=None):
    # Markers must be actual line comments, never text inside a string/comment.
    tokens = {match.start(): match.group() for match in TOKEN.finditer(source)}
    if tokens.get(position) != marker.rstrip('\n'):
        raise ValueError('Managed gesture markers must be active top-level comments')
    structure = masked(source if context is None else context, strings=True)
    if re.search(r'\b(function|if|for|while|repeat|goto|do|return)\b', structure):
        raise ValueError('Conditional or function-based configuration requires manual gesture management')
    prefix = structure[:position]
    if any(prefix.count(a) != prefix.count(b) for a, b in [('(', ')'), ('{', '}'), ('[', ']')]):
        raise ValueError('Nested gesture configuration requires manual management')


def parse(source):
    if BEGIN in source or END in source:
        if source.count(BEGIN) != 1 or source.count(END) != 1:
            raise ValueError('The Trackpad Plus gesture block has been modified; restore it before editing')
        start, end = source.index(BEGIN), source.index(END) + len(END)
        if end < start:
            raise ValueError('Invalid Trackpad Plus gesture block')
        fragment = source[start:end]
        try:
            data = json.loads(fragment.splitlines()[1][3:])
            if (set(data) != {'version', 'settings', 'original', 'separator'}
                    or type(data['version']) is not int or data['version'] not in (2, 3, 4, 5, 6, 7, 8)
                    or not isinstance(data['original'], str)):
                raise ValueError('Unsupported gesture block version')
            if fragment != block(data['settings'], data['original'], data['separator'], data['version']):
                raise ValueError('The Trackpad Plus gesture block has been manually modified')
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('Invalid Trackpad Plus gesture block') from exc
        # Only the exact canonical block may contain our optional-provider guard.
        # Outside it, conditional/nested user configuration still requires manual management.
        context = source[:start] + re.sub(r'[^\n]', ' ', fragment) + source[end:]
        top_level_marker(source, start, BEGIN, context)
        top_level_marker(source, end - len(END), END, context)
        separator = data['separator']
        if separator:
            if start == 0 or source[start-1:start] != separator:
                raise ValueError('The managed gesture separator has been modified')
            start -= len(separator)
        if data['original']:
            if source.count(ANCHOR) != 1 or source.index(ANCHOR) >= start:
                raise ValueError('The original gesture anchor has been moved or modified')
            top_level_marker(source, source.index(ANCHOR), ANCHOR, context)
        elif ANCHOR in source:
            raise ValueError('Unexpected original gesture anchor')
        if find_binding(source[:start] + source[end:]):
            raise ValueError('Another workspace binding exists outside the managed block')
        return start, end, data['settings'], data['original']
    if ANCHOR in source:
        raise ValueError('Orphaned original gesture anchor requires manual recovery')
    return None


def inspect(source):
    result = dict(managed=False, can_edit=True, can_restore=False, message='',
                  settings=dict(enabled=False, fingers=3, distance=300, invert=False, overview=False,
                                fullscreen_up=False, scratchpad_down=False),
                  hymission=hymission_status(), companion=companion_status())
    try:
        managed = parse(source)
        if managed:
            result.update(managed=True, can_restore=True, settings=normalized(managed[2]),
                          message='Managed by Trackpad Plus · applies across trackpads')
        else:
            result['settings'].update(runtime_settings())
            existing = find_binding(source)
            if existing:
                result['settings'].update(enabled=True, fingers=existing[2])
                result['message'] = 'Existing workspace swipe · Apply adopts it with a backup'
            else:
                result['message'] = 'No workspace swipe configured · Apply enables your choices'
        check_environment()
    except (ValueError, RuntimeError, OSError) as exc:
        # A valid managed block can still be restored when external configuration
        # prevents new edits. Keep its acknowledged settings available, too.
        result.update(can_edit=False, message=str(exc))
    return result


config_target = core.config_target  # Shared with pointer profiles; Stow-aware.


def config_files():
    """Include Stow directory links in conflict checks without following cycles."""
    root = config_target(CONFIG)
    pending, seen, count = [root], set(), 0
    while pending:
        alias = pending.pop()
        count += 1
        if count > 4096:
            raise ValueError('Too many Hyprland configuration entries to check safely')
        try:
            path = config_target(alias)
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            # A vanished wallpaper or backup cannot contain active gestures.
            # Missing config entries and all trust failures still block Apply.
            if alias == root or alias.suffix in ('.lua', '.conf'):
                raise
            continue
        if stat.S_ISDIR(info.st_mode):
            if path in seen:
                continue
            seen.add(path)
            with core.state_directory(path) as directory:
                pending.extend(path / name for name in os.listdir(directory))
        elif alias.suffix in ('.lua', '.conf'):
            yield path, alias.suffix


def check_environment():
    # Reject known conflicts from other local modules; never edit those files.
    input_target = config_target(INPUT)
    for path, suffix in config_files():
        if path == input_target:
            continue
        source = core.read_state_file(path) or ''
        conflict = (re.search(r'^\s*(?:gesture|workspace_swipe)\s*=', source, re.MULTILINE)
                    if suffix == '.conf' else re.search(r'\bgesture\b', masked(source, strings=True)))
        if conflict:
            raise ValueError('Gestures also exist in another Hyprland file; manage them there to avoid conflicts')
    core.hypr('eval', 'assert(package.loaded["hypr.input"], "Trackpad Plus requires the hypr.input module")')


def reload_checked():
    core.hypr('reload', 'config-only')
    errors = core.hypr('configerrors').strip()
    if errors:
        raise RuntimeError('Hyprland rejected the gesture settings: ' + errors)


def clear_journal():
    with core.state_directory(JOURNAL.parent) as directory:
        os.unlink(JOURNAL.name, dir_fd=directory)
        os.fsync(directory)


def recover():
    raw = core.read_state_file(JOURNAL)
    if raw is None:
        return
    data = json.loads(raw)
    if (not isinstance(data, dict) or type(data.get('version')) is not int
            or data['version'] not in (1, 2)
            or set(data) != ({'version', 'before', 'after'} if data['version'] == 1
                             else {'version', 'before', 'after', 'target'})
            or not all(isinstance(data[key], str) for key in ('before', 'after'))):
        raise ValueError('Invalid gesture recovery journal')
    target = config_target(INPUT)
    # Old journals could only be created for a non-linked config. Never replay
    # either journal format onto a newly selected dotfile, even with equal text.
    expected_target = data['target'] if data['version'] == 2 else str(INPUT)
    if str(target) != expected_target:
        raise ValueError('Input configuration target changed during recovery; restore the original link and retry')
    current = core.read_state_file(target)
    if current not in (data['before'], data['after']):
        raise ValueError('Input configuration changed during recovery; preserve it and restore the backup manually')
    if current != data['before']:
        core.atomic_write(target, data['before'])
    reload_checked()
    if config_target(INPUT) != target:
        raise ValueError('Input configuration target changed during recovery; restore the original link and retry')
    clear_journal()


def transact(before, after, target, expected=None):
    errors = core.hypr('configerrors').strip()
    if errors:
        raise ValueError('Resolve existing Hyprland configuration errors before editing gestures')
    if config_target(INPUT) != target:
        raise ValueError('Input configuration target changed; refresh and try again')
    if core.read_state_file(target) != before:
        raise ValueError('Input configuration changed; refresh and try again')
    if core.read_state_file(BACKUP) is None:
        core.atomic_write(BACKUP, before)
    core.atomic_write(JOURNAL, json.dumps(dict(version=2, before=before, after=after, target=str(target))))
    try:
        if config_target(INPUT) != target:
            raise ValueError('Input configuration target changed; refresh and try again')
        core.atomic_write(target, after)
        reload_checked()
        if config_target(INPUT) != target:
            raise ValueError('Input configuration target changed during reload')
        if expected is not None:
            actual = runtime_settings()
            if any(actual[key] != expected[key] for key in ('distance', 'invert')):
                raise RuntimeError('Runtime gesture settings do not match; another configuration may override them')
            if expected.get('overview') and overview_provider(expected) == 'hymission' and not hymission_status()['available']:
                raise RuntimeError('HyMission became unavailable while applying overview gestures')
        clear_journal()
    except Exception as original:
        try:
            recover()
        except Exception as rollback:
            raise RuntimeError(f'{original}; gesture recovery pending: {rollback}') from original
        raise


def change(settings):
    settings = normalized(settings)
    target = config_target(INPUT)
    before = core.read_state_file(target)
    if before is None:
        raise ValueError('Missing Hyprland input.lua')
    status = inspect(before)
    if not status['can_edit']:
        raise ValueError(status['message'])
    managed = parse(before)
    # Only an explicit edit emits schema 8. An older caller editing a managed
    # block retains its current provider instead of silently reverting it.
    settings['overview_provider'] = settings.get('overview_provider', overview_provider(managed[2]) if managed else 'hymission')
    if settings['overview']:
        provider = status['companion'] if overview_provider(settings) == 'trackpad-plus' else status['hymission']
        ready = provider.get('installed') and not provider.get('error') if overview_provider(settings) == 'trackpad-plus' else provider.get('available')
        if not ready:
            raise ValueError(provider.get('message') or 'Overview is unavailable; test it before applying gestures')
    if managed:
        start, end, _, original = managed
        base = before[:start] + before[end:]
    else:
        existing = find_binding(before)
        if existing:
            start, end = existing[:2]
            if before[end:end+1] == '\n': end += 1
            original = before[start:end]
            base = before[:start] + ANCHOR + before[end:]
        else:
            original, base = '', before
    if settings['overview'] or settings.get('fullscreen_up') or settings.get('scratchpad_down'):
        # HyMission replaces matching native registrations. Never let an explicit
        # vertical gesture opt-in replace a user's separate vertical gesture silently.
        # The lenient check also catches four-field bindings such as the manual
        # scratchpad line (direction down plus workspace_name), which the strict
        # adoption parser cannot represent.
        owner = 'overview' if settings['overview'] else 'fullscreen/scratchpad actions'
        code, structure = masked(base), masked(base, strings=True)
        for call in CALL.finditer(code):
            if structure[call.start():call.start()+len('hl.gesture')] != 'hl.gesture':
                continue
            try:
                fields = parse_call(call[1])
            except ValueError:
                if re.search(r'direction\s*=\s*["\'](vertical|up|down|swipe)', call[1]):
                    raise ValueError('An existing vertical gesture conflicts with %s; manage it in your Hyprland config' % owner)
                raise
            if fields['direction'] in ('vertical', 'up', 'down', 'swipe'):
                raise ValueError('An existing vertical gesture conflicts with %s; manage it in your Hyprland config' % owner)
    separator = '\n' if base and not base.endswith('\n') else ''
    after = base + separator + block(settings, original, separator, version=8)
    # Validate the complete result before writing; appending to dynamic Lua is unsafe.
    parse(after)
    transact(before, after, target, expected=settings)


def restore():
    target = config_target(INPUT)
    before = core.read_state_file(target)
    managed = parse(before or '')
    if not managed:
        raise ValueError('No managed gestures to restore')
    start, end, _, original = managed
    after = before[:start] + before[end:]
    if original:
        after = after.replace(ANCHOR, original, 1)
    transact(before, after, target)


def main():
    args = sys.argv[1:]
    if args == ['preview'] or args == ['overview-status']:
        # Explicit preview is outside the settings lock and never mutates config.
        print(json.dumps(companion_status('open' if args[0] == 'preview' else 'status')))
        return
    if args == ['state'] or args == ['restore']:
        pass
    elif len(args) == 2 and args[0] == 'set':
        validate(json.loads(args[1]))
    else:
        raise ValueError('Usage: gestures.py state|restore|preview|overview-status|set JSON_SETTINGS')
    with core.state_lock():
        recover()
        if args[0] == 'set': change(json.loads(args[1]))
        elif args[0] == 'restore': restore()
        source = core.read_state_file(config_target(INPUT))
        if source is None:
            raise ValueError('Missing Hyprland input.lua')
        print(json.dumps(inspect(source)))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(json.dumps(dict(error=str(exc))))
        sys.exit(1)
