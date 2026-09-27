<p align="center">
  <a href="https://shop.freshapp.io">
    <img src="docs/brand/freshapp-io-logo.png" alt="FreshApp.io" width="280">
  </a>
</p>

<p align="center">
  <a href="#francais">🇫🇷 Français</a> · <a href="#english">🇬🇧 English</a>
</p>

---

<a id="francais"></a>
## 🇫🇷 Français

# Freshapp Music Tagger

Application web (Docker) pour nettoyer les tags d'une grosse bibliothèque MP3,
typiquement servie par [Navidrome](https://www.navidrome.org/).

- **Nom technique** : `freshapp-music-tagger`
- **Version** : 1.0.0
- **Auteur** : FreshApp.io

### Fonctionnement

L'application scanne une arborescence et considère **chaque dossier contenant
des MP3 comme un album**. Elle détecte :

| Page | Ce qui est détecté |
|---|---|
| **Non taggués** | fichiers sans tag ID3, ou sans artiste / titre / album |
| **Various Artists** | album artist ou artiste « Various Artists » (VA, Divers…), sauf les vraies compilations déjà propres |
| **Tags incohérents** | dans un même dossier : album artist, nom d'album, année ou ID MusicBrainz différents — ce qui fait éclater l'album en plusieurs dans Navidrome |
| **Doublons** | même album présent dans plusieurs dossiers |
| **Genres** | genres écrits de mille façons (`Hip-Hop/Rap`, `Rap & Hip-Hop`, `hiphop`…), génériques (`Other`, `Unknown`) ou farfelus |

Pour chaque dossier, une **suggestion** est calculée avec un niveau de confiance :
artiste majoritaire (hors « feat. »), album et année majoritaires ou déduits du
nom du dossier, titres et numéros de piste déduits des noms de fichiers.

### Corriger

- **En lot** : filtrer (par exemple « suggestion sûre »), tout sélectionner, appliquer.
- **Album par album** : le mode revue fait défiler les albums du filtre un par un,
  suggestion pré-remplie et modifiable, avec *Valider et suivant*, *Passer*,
  *Ne plus proposer* (raccourcis Ctrl+Entrée, Ctrl+→, Ctrl+I).
- **Piste par piste** : pour les compilations, chaque morceau se tague et
  s'enregistre séparément, avec suggestion depuis le nom de fichier ou recherche
  du morceau sur MusicBrainz.
- **MusicBrainz** : recherche de l'album, association fichiers ↔ pistes
  (numéro, titre, durée), tags complets avec identifiants MusicBrainz, pochette
  depuis Cover Art Archive.
- **Doublons** : la version de meilleure qualité est présélectionnée (bitrate,
  nombre de pistes, pochette, tags complets) ; les autres vont à la corbeille.
- **Genres** : chaque valeur est rapprochée d'une liste de genres cibles
  modifiable. Les genres vides ou farfelus reprennent le genre habituel de
  l'artiste, du dossier, ou celui trouvé sur MusicBrainz.
- **Écoute** : lecteur intégré sur chaque piste.

### Sécurité des données

- Chaque écriture est journalisée avec l'ancienne valeur de chaque champ :
  **Historique → Annuler** restaure les tags.
- Rien n'est supprimé : les doublons sont **déplacés** dans
  `.music-tagger-trash/` à la racine de la bibliothèque, avec un fichier
  `.ndignore` pour que Navidrome l'ignore. Annulable tant que la corbeille
  n'est pas vidée.
- Seuls les champs modifiés sont réécrits ; la version ID3 existante (2.3 / 2.4)
  est conservée.
- Un scan ne retire jamais de l'index le contenu d'un dossier devenu
  illisible, et s'interrompt si la bibliothèque est vide ou inaccessible.

### Installation

```bash
git clone https://github.com/Freshapp-io/music-tagger.git
cd music-tagger
cp .env.example .env    # puis éditer
docker compose up -d --build
```

Ouvrir ensuite `http://<hôte>:8085` et lancer un **scan**.

Deux façons de donner accès à la bibliothèque, à choisir dans `docker-compose.yml` :

- **A) Sur le NAS** (recommandé, accès disque local) : `MUSIC_PATH` = chemin du
  dossier partagé sur le NAS (ex. `/volume1/Medias` sur Synology).
- **B) Sur un PC** (Docker Desktop, WSL) : le conteneur monte lui-même le partage
  SMB (`SMB_HOST`, `SMB_SHARE`, `SMB_USER`, `SMB_PASSWORD`). Un lecteur réseau
  Windows (`Z:\`) n'est pas visible par Docker.

### Configuration

| Variable | Défaut | Rôle |
|---|---|---|
| `MUSIC_SUBDIR` | — | sous-dossier du volume qui contient la bibliothèque |
| `PUID` / `PGID` | 1000 | utilisateur qui écrit les fichiers |
| `PORT` | 8085 | port web |
| `SCAN_THREADS` | 8 | lectures parallèles pendant le scan |
| `MB_USER_AGENT` | — | contact transmis à MusicBrainz |
| `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASSWORD` | — | optionnel : bouton « Lancer un scan Navidrome » (compte administrateur) |

La base SQLite (index des tags, historique, choix de genres) est dans `./data`.

### Limites

- Seuls les **MP3** sont traités.
- Un dossier « Artiste » contenant des titres en vrac apparaît comme incohérent :
  il suffit de l'ignorer.
- Les albums multi-CD rangés dans `CD1/`, `CD2/` sont vus comme deux dossiers,
  mais ne sont pas pris pour des doublons.

### Développement

```bash
docker build -t freshapp-music-tagger .
docker run --rm -u 0 -v "$PWD":/src -w /src freshapp-music-tagger \
  sh -c "pip install -q pytest && python -m pytest -q tests"
```

Python 3.12, FastAPI, mutagen, SQLite ; interface en JavaScript sans étape de build (`app/static`).

Retrouvez nos modules sur **[shop.freshapp.io](https://shop.freshapp.io)**.

---

<a id="english"></a>
## 🇬🇧 English

# Freshapp Music Tagger

Dockerised web app to clean up the tags of a large MP3 library, typically
served by [Navidrome](https://www.navidrome.org/).

- **Technical name**: `freshapp-music-tagger`
- **Version**: 1.0.0
- **Author**: FreshApp.io

### How it works

The app scans a folder tree and treats **every folder containing MP3 files as
an album**. It detects:

| Page | What is detected |
|---|---|
| **Untagged** | files without any ID3 tag, or missing artist / title / album |
| **Various Artists** | album artist or artist set to "Various Artists" (VA…), except genuine compilations that are already clean |
| **Inconsistent tags** | within one folder: different album artist, album name, year or MusicBrainz ID — which makes Navidrome split the album |
| **Duplicates** | the same album stored in several folders |
| **Genres** | genres spelled in countless ways (`Hip-Hop/Rap`, `Rap & Hip-Hop`, `hiphop`…), generic (`Other`, `Unknown`) or odd ones |

For each folder a **suggestion** is computed with a confidence level: main
artist (ignoring "feat."), most common album and year or values derived from
the folder name, titles and track numbers derived from file names.

### Fixing

- **In bulk**: filter (e.g. "confident suggestion"), select all, apply.
- **Album by album**: review mode steps through the filtered albums one at a
  time, with the suggestion pre-filled and editable: *Validate and next*,
  *Skip*, *Don't suggest again* (Ctrl+Enter, Ctrl+→, Ctrl+I).
- **Track by track**: for compilations, each track is tagged and saved on its
  own, with a suggestion from the file name or a MusicBrainz track search.
- **MusicBrainz**: album search, automatic file ↔ track matching (number, title,
  duration), full tags with MusicBrainz IDs, cover art from Cover Art Archive.
- **Duplicates**: the best-quality version is pre-selected (bitrate, number of
  tracks, cover, complete tags); the others go to the trash folder.
- **Genres**: every value is mapped onto an editable list of target genres.
  Empty or odd genres take the artist's usual genre, the folder's, or the one
  found on MusicBrainz.
- **Listening**: built-in player on every track.

### Data safety

- Every write is logged with the previous value of each field:
  **History → Undo** restores the tags.
- Nothing is deleted: duplicates are **moved** to `.music-tagger-trash/` at the
  library root, with an `.ndignore` file so that Navidrome skips it. Undoable
  until the trash is emptied.
- Only modified fields are rewritten; the existing ID3 version (2.3 / 2.4) is
  kept.
- A scan never drops the content of a folder that became unreadable from the
  index, and aborts if the library is empty or unreachable.

### Installation

```bash
git clone https://github.com/Freshapp-io/music-tagger.git
cd music-tagger
cp .env.example .env    # then edit
docker compose up -d --build
```

Then open `http://<host>:8085` and run a **scan**.

Two ways to give the container access to the library, chosen in `docker-compose.yml`:

- **A) On the NAS** (recommended, local disk access): `MUSIC_PATH` = path of
  the shared folder on the NAS (e.g. `/volume1/Medias` on Synology).
- **B) On a PC** (Docker Desktop, WSL): the container mounts the SMB share
  itself (`SMB_HOST`, `SMB_SHARE`, `SMB_USER`, `SMB_PASSWORD`). A Windows
  network drive (`Z:\`) is not visible to Docker.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MUSIC_SUBDIR` | — | sub-folder of the volume holding the library |
| `PUID` / `PGID` | 1000 | user that writes the files |
| `PORT` | 8085 | web port |
| `SCAN_THREADS` | 8 | parallel reads during a scan |
| `MB_USER_AGENT` | — | contact sent to MusicBrainz |
| `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASSWORD` | — | optional: "Start a Navidrome scan" button (admin account) |

The SQLite database (tag index, history, genre choices) lives in `./data`.

### Limitations

- Only **MP3** files are handled.
- An "Artist" folder holding loose tracks shows up as inconsistent: just
  ignore it.
- Multi-disc albums stored in `CD1/`, `CD2/` are seen as two folders, but are
  not reported as duplicates.

### Development

```bash
docker build -t freshapp-music-tagger .
docker run --rm -u 0 -v "$PWD":/src -w /src freshapp-music-tagger \
  sh -c "pip install -q pytest && python -m pytest -q tests"
```

Python 3.12, FastAPI, mutagen, SQLite; JavaScript front-end with no build step (`app/static`).

Find our modules on **[shop.freshapp.io](https://shop.freshapp.io)**.

---

## Licence / License

GPL-3.0-or-later — voir / see [LICENSE](LICENSE).

Polices / Fonts: Fira Sans and Rajdhani, SIL Open Font License (`app/static/fonts`).
