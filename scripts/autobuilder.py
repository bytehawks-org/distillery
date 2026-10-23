#!/usr/bin/env python3
import yaml
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Layout: /opt/bytehawks/core/distillery/scripts/autobuilder.py
DISTILLERY_HOME = Path(__file__).resolve().parent.parent

RUNTIME_TYPES = ('string', 'secret', 'int', 'uint', 'port', 'bool', 'path')
ENV_NAME_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')


def load_yaml(filepath):
    """Carica un file YAML"""
    with open(filepath, 'r') as f:
        return yaml.safe_load(f)


def resolve_template(template, context, max_depth=10):
    """Risolve template ricorsivamente sostituendo {{ var }} con valori dal context"""
    if not isinstance(template, str):
        return template

    for _ in range(max_depth):
        # Trova tutti i placeholder {{ ... }} con il pattern completo
        pattern = r'\{\{\s*([^}]+?)\s*\}\}'
        matches = list(re.finditer(pattern, template))

        if not matches:
            break

        # Processa i match in ordine inverso per non invalidare gli indici
        for match in reversed(matches):
            placeholder = match.group(0)  # Es: "{{ config.default_download_path }}"
            var_path = match.group(1).strip()  # Es: "config.default_download_path"

            # Naviga il context seguendo il path
            keys = var_path.split('.')
            value = context
            try:
                for key in keys:
                    value = value[key]

                # Risolvi ricorsivamente se il valore è ancora un template
                if isinstance(value, str) and '{{' in value:
                    value = resolve_template(value, context, max_depth - 1)

                # Sostituisci il placeholder esatto trovato
                template = template.replace(placeholder, str(value))

            except (KeyError, TypeError) as e:
                # Variabile non trovata, lascia il placeholder
                print(f"Warning: impossibile risolvere '{var_path}': {e}")
                pass

    return template


def resolve_dict_templates(data, context):
    """Risolve ricorsivamente tutti i template in un dizionario"""
    if isinstance(data, dict):
        return {k: resolve_dict_templates(v, context) for k, v in data.items()}
    elif isinstance(data, list):
        return [resolve_dict_templates(item, context) for item in data]
    elif isinstance(data, str):
        return resolve_template(data, context)
    else:
        return data


def build_context(config, package_data, package_name, variant_name, tag):
    """Costruisce il context per la risoluzione dei template"""
    # Trova la variant specifica
    variant = None
    for v in package_data['variant']:
        if variant_name in v['name'] if isinstance(v['name'], list) else variant_name == v['name']:
            variant = v
            break

    if not variant:
        raise ValueError(f"Variant '{variant_name}' non trovata")

    # Determina il prefix in base al tag
    if tag == 'library':
        base_prefix = config['config']['libraries']['default_prefix']
    elif tag == 'application':
        base_prefix = config['config']['applications']['default_prefix']
    else:
        raise ValueError(f"Tag '{tag}' non valido")

    # Costruisce il context base unendo config, package_data e variant
    context = {
        'config': config['config'],
        'package_name': package_name,
        'name': package_name,
        'default_prefix': base_prefix,
        'full_version': variant['full_version'],
        'major_version': variant['major_version'],
        'patch_version': variant['patch_version'],
        'git': package_data.get('git', {}),
        'website': package_data.get('website', ''),
    }

    # Risolvi tutti i template presenti nel context stesso (come git.release_download_url_template)
    context['git'] = resolve_dict_templates(context['git'], context)

    return context, variant


def execute_commands(commands_dict, context):
    """Esegue i comandi specificati nel dizionario"""
    # import subprocess  # Decommentare per esecuzione reale

    for stage, command_template in commands_dict.items():
        print(f"\n{'='*60}")
        print(f"Stage: {stage}")
        print(f"{'='*60}")

        # Risolvi il template del comando
        command = resolve_template(command_template, context)
        print(command)

        # Decommentare per eseguire realmente i comandi
        # try:
        #     result = subprocess.run(
        #         command,
        #         shell=True,
        #         check=True,
        #         text=True,
        #         capture_output=True
        #     )
        #     print(result.stdout)
        # except subprocess.CalledProcessError as e:
        #     print(f"Errore durante l'esecuzione: {e}")
        #     print(f"Output: {e.output}")
        #     raise


def rosetta_lines(package_name, package_data, context):
    """Compila runtime.env nel formato Rosetta letto da Shaker (env|type|default|flags|aliases|file_key|cli|native_env|description)"""
    runtime = resolve_dict_templates(package_data.get('runtime', {}), context)
    prefix = 'BH_' + package_name.upper().replace('-', '_') + '_'
    lines = ['# Generated by Distillery - do not edit',
             '# env|type|default|flags|aliases|file_key|cli|native_env|description']
    seen = set()

    for item in runtime.get('env', []):
        name = item.get('name', '')
        if not ENV_NAME_RE.match(name) or not name.startswith(prefix):
            raise ValueError(f"runtime.env: '{name}' deve iniziare con {prefix}")
        if name in seen:
            raise ValueError(f"runtime.env: '{name}' duplicato")
        seen.add(name)

        vtype = str(item.get('type', 'string'))
        if vtype not in RUNTIME_TYPES and not (vtype.startswith('enum:') and len(vtype) > 5):
            raise ValueError(f"runtime.env {name}: type '{vtype}' non valido")

        flags = ['required'] if item.get('required') else []
        fields = [name, vtype,
                  '' if item.get('default') is None else str(item['default']).lower() if isinstance(item.get('default'), bool) else str(item['default']),
                  ','.join(flags),
                  ','.join(item.get('aliases', [])),
                  item.get('file_key', ''),
                  item.get('cli', ''),
                  item.get('native_env', ''),
                  item.get('description', '')]
        if any('|' in f or '\n' in f for f in fields):
            raise ValueError(f"runtime.env {name}: i caratteri '|' e newline non sono ammessi")
        lines.append('|'.join(fields))

    return lines


def emit_bundle(package_name, package_data, variant, context, out_dir):
    """Scrive <out_dir>/rosetta.map e manifest.json: il contratto Distillery -> Shaker"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / 'rosetta.map').write_text('\n'.join(rosetta_lines(package_name, package_data, context)) + '\n')

    recipe = resolve_dict_templates({k: v for k, v in package_data.items() if k != 'variant'}, context)
    recipe_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    manifest = {
        'schema': 1,
        'generator': 'distillery',
        'package': package_name,
        'variant': variant['name'],
        'version': str(variant['full_version']),
        'tags': package_data.get('tags', []),
        'prefix': context['default_prefix'],
        'source': context['git'].get('release_download_url_template', ''),
        'sha256': variant.get('sha256', ''),
        'recipe_sha256': recipe_hash,
    }
    if os.environ.get('SOURCE_DATE_EPOCH'):
        manifest['built_at_epoch'] = int(os.environ['SOURCE_DATE_EPOCH'])
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(f"Bundle scritto in {out}")


def main():
    parser = argparse.ArgumentParser(description='ByteHawks build orchestrator')
    sub = parser.add_subparsers(dest='action')

    def common(p):
        p.add_argument('--package', required=True, help='Nome del package (es: openssl)')
        p.add_argument('--variant', default='stable', help='Variant (es: stable, legacy)')
        p.add_argument('--tag', required=True, choices=['library', 'application'],
                       help='Tag per determinare il prefix')
        p.add_argument('--config', default=str(DISTILLERY_HOME / 'config.yaml'), help='Path al file config.yaml')
        p.add_argument('--packages', default=str(DISTILLERY_HOME / 'packages.yaml'), help='Path al file packages.yaml')

    common(sub.add_parser('build', help='Mostra/esegue i comandi di build'))
    p_bundle = sub.add_parser('bundle', help='Genera il bundle runtime per Shaker (rosetta.map, manifest.json)')
    common(p_bundle)
    p_bundle.add_argument('--out', help='Directory di output (default: <prefix>/.bytehawks)')

    # Compatibilita' con la vecchia invocazione senza sotto-comando
    argv = sys.argv[1:]
    if argv and argv[0].startswith('--') and argv[0] not in ('-h', '--help'):
        argv = ['build'] + argv
    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 2

    config = load_yaml(args.config)
    packages = load_yaml(args.packages)

    package_data = packages['package'].get(args.package)
    if not package_data:
        raise ValueError(f"Package '{args.package}' non trovato in packages.yaml")

    if args.tag not in package_data.get('tags', []):
        print(f"Attenzione: il tag '{args.tag}' non e' presente nei tags del package")

    context, variant = build_context(config, package_data, args.package, args.variant, args.tag)

    if args.action == 'bundle':
        emit_bundle(args.package, package_data, variant, context,
                    args.out or Path(context['default_prefix']) / '.bytehawks')
        return 0

    print(f"Building {args.package} - variant: {args.variant} ({variant['full_version']})")
    print(f"Prefix: {context['default_prefix']}")
    execute_commands(package_data['commands'], context)
    return 0


if __name__ == '__main__':
    sys.exit(main())
