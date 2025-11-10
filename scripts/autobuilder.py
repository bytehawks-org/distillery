#!/usr/bin/env python3
import yaml
import argparse
import re
from pathlib import Path


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


def main():
    parser = argparse.ArgumentParser(description='ByteHawks build orchestrator')
    parser.add_argument('--package', required=True, help='Nome del package (es: openssl)')
    parser.add_argument('--variant', default='stable', required=True, help='Variant da buildare (es: stable, legacy)')
    parser.add_argument('--tag', required=True, choices=['library', 'application'],
                       help='Tag per determinare il prefix')
    parser.add_argument('--config', default='config.yaml', help='Path al file config.yaml')
    parser.add_argument('--packages', default='packages.yaml', help='Path al file packages.yaml')

    args = parser.parse_args()

    # Carica i file di configurazione
    config = load_yaml(args.config)
    packages = load_yaml(args.packages)

    # Recupera i dati del package
    package_data = packages['package'].get(args.package)
    if not package_data:
        raise ValueError(f"Package '{args.package}' non trovato in packages.yaml")

    # Verifica che il tag sia presente
    if args.tag not in package_data.get('tags', []):
        print(f"Attenzione: il tag '{args.tag}' non è presente nei tags del package")

    # Costruisce il context per i template
    context, variant = build_context(config, package_data, args.package, args.variant, args.tag)

    print(f"Building {args.package} - variant: {args.variant} ({variant['full_version']})")
    print(f"Prefix: {context['default_prefix']}")

    # Esegue i comandi
    execute_commands(package_data['commands'], context)


if __name__ == '__main__':
    main()
