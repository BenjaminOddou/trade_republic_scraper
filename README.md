# Trade Republic Scraper

## Description

Ce projet permet d'extraire et de sauvegarder les transactions depuis l'API WebSocket de Trade Republic dans un fichier JSON ou CSV. Il nécessite une connexion à l'API via un numéro de téléphone et un code PIN. Une fois connecté, le script récupère toutes les transactions et les sauvegarde dans le format spécifié.

## Prérequis

- Python 3.
- Libraries pandas, websockets et requests

## Installation

1. Téléchargez ce projet en local sur votre machine.

2. Renommez le fichier `exemple.ini` en `config.ini` et remplissez les champs suivants :

- `phone_number`: Votre numéro de téléphone utilisé pour la connexion à Trade Republic.
- `pin`: Votre code PIN de Trade Republic.
- `output_format`: Le format de sortie des données (json ou csv).
- `output_folder`: Le dossier où les données exportées seront sauvegardées.
- `extract_details`: Active la récupération du détail des transactions via l’appel timelineDetailV2 pour un historique plus complet (nombre de titres, cours du titre, impôt, etc.). Cela ralentit cependant le processus de collecte des informations.

Exemple:

```ini
[secret]
phone_number = +33600000000
pin = 1234

[general]
output_format = csv
output_folder = out
extract_details = true
```

Ouvrez votre invite de commandes / terminal, naviguez jusqu’au dossier `trade_republic_scraper` et installez les dépendances Python nécessaires :

MacOS

```bash
pip3 install -r requirements.txt -t lib
```

Windows

```bash
pip install requirements.txt -t lib
```

## Utilisation

Exécutez le script python `main.py` :

MacOS

```bash
python3 main.py
```

Windows

```bash
python main.py
```

Le script se connectera à l'API, vous demandera un code 2FA que vous recevrez dans l'application Trade Republic ou par SMS, et extraira toutes vos transactions sur votre machine.
Les données seront sauvegardées dans le dossier spécifié sous le format choisi (json ou csv).

## Fonctionnalités

- Connexion à l'API Trade Republic via WebSocket.
- Extraction des transactions et des données associées.
- Support pour les formats de sortie JSON et CSV.
- Conversion des dates et des montants au format français (DD/MM/YYYY).
