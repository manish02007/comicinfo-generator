# 📚 ComicInfo Generator — GUI Edition

A powerful **GUI tool** to generate and embed `ComicInfo.xml` metadata into `.cbz` comic files — built with Python and Tkinter.

---

## ✨ Features

- 🖥️ Modern GUI (no CLI needed)
- 📦 Batch process multiple `.cbz` files
- 🏷️ Automatic metadata generation
- 🔢 Smart chapter/volume detection
- 🧠 Decimal chapter handling (e.g. 10.5, 12.1)
- 📚 Volume, date, and summary rules
- 📝 Custom XML fields support
- 🔄 Resume interrupted runs
- ⚡ Parallel processing
- 🧪 Dry-run mode (preview changes safely)
- 🎯 Designed for Komga / Kavita compatibility

---

## 📥 Download

Download the appropriate file:

- 🪟 Windows → `comicinfo_generator.exe`
- 🐧 Linux → 
  - `comicinfo-generator-linux.tar.gz`
  - `comicinfo-generator.deb`
  - `comicinfo-generator.rpm`

---

## 🚀 Usage

### 🪟 Windows

1. Download `comicinfo_gui.exe`
2. Double-click to run
3. No installation required

---

### 🐧 Linux

#### Option 1 — Run directly

```bash
tar -xzf comicinfo-linux.tar.gz
./comicinfo_gui
```

#### Option 2 — Install (.deb)

```bash
sudo dpkg -i comicinfo.deb
```

#### Option 3 — Install (.rpm)

```bash
sudo rpm -i comicinfo.rpm
```

---

## ⚠️ Requirements (Linux only)

Tkinter is required:

```bash
sudo apt install python3-tk
```

---

## 🛠️ How It Works

The tool:

1. Reads `.cbz` files
2. Extracts chapter/volume info from filenames
3. Generates `ComicInfo.xml`
4. Embeds metadata into archive
5. Optionally renames files

---

## 📁 Supported Metadata

- Title
- Series
- Number
- Volume
- Writer / Penciller
- Publisher
- Language
- Genre / Rating
- Date (Year / Month / Day)
- Summary
- Custom fields

---

## ⚙️ Configuration

You can:

- Load chapter titles from JSON
- Define volume rules
- Define date rules
- Define summary rules
- Customize naming formats

---

## 🧪 Development

### Clone repo

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

### Run locally

```bash
python comicinfo_gui.py
```

### Build manually

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole comicinfo_gui.py
```

---

## ⚠️ Notes

- Antivirus may flag the `.exe` (common with PyInstaller)
- Linux builds require Tkinter installed
- RPM/DEB packages are minimal (no desktop integration yet)

---

## 📌 Roadmap

- [ ] AppImage support (better Linux distribution)
- [ ] Improved packaging (desktop icons, menu entries)
- [ ] Performance optimizations
- [ ] Themes 

---

## 📄 License

MIT License — free to use, modify, and distribute.
