# metarace-roadmeet

Timing and result application for UCI Part 2 Road Races,
UCI Part 5 Cyclo-Cross, criterium, road handicap and
ad-hoc time trial events.

![roadmeet screenshot](screenshot.png "roadmeet")


## Usage

Create a new meet and open it:

	$ roadmeet

Open an existing road meet:

	$ roadmeet PATH

Edit default configuration:

	$ roadmeet --edit-default


## Support

   - Signal Group: [metarace](https://signal.group/#CjQKII2j2E7Zxn7dHgsazfKlrIXfhjgZOUB3OUFhzKyb-p_bEhBehsI65MhGABZaJeJ-tMZl)
   - Github Issues: [issues](https://github.com/ndf-zz/metarace-roadmeet/issues)


## Requirements

   - Python >= 3.9
   - PyGObject
   - Gtk >= 3.0
   - metarace > 2.1
   - tex-gyre fonts (optional, recommended)
   - evince (optional, recommended)
   - rsync (optional)
   - mosquitto (optional)


## Automated Installation

For semi-automated installation on common unix-like
desktop systems, download the [install script](install.sh)
and run with sh:

	$ sh install.sh


## Manual Installation

Install system requirements for your OS (See
[System-Specific Preparation](#system-specific-preparaton)
below) then prepare a metarace runtime directory
and virtual env as follows:

	$ mkdir -p ~/Documents/metarace
	$ python3 -m venv --system-site-packages ~/Documents/metarace/venv

Install roadmeet to the virtual env, or run from
source using one of the following methods:


### Install From PyPI With Pip

Use pip in your virtual env to download and install
roadmeet along with any required python packages
from the Python Package Index:

	$ ~/Documents/metarace/venv/bin/pip3 install metarace-roadmeet

Create a new empty roadmeet:

	$ ~/Documents/metarace/venv/bin/roadmeet


### Install From Wheel

Download the roadmeet wheel from github and verify signature:

	$ wget https://github.com/ndf-zz/metarace-roadmeet/releases/download/v1.13.3/metarace_roadmeet-1.13.3-py3-none-any.whl
	$ wget https://github.com/ndf-zz/metarace-roadmeet/releases/download/v1.13.3/metarace_roadmeet-1.13.3-py3-none-any.whl.asc
	$ gpg --verify metarace_roadmeet-1.13.3-py3-none-any.whl.asc

Use pip in your virtual env to install the roadmeet wheel:

	$ ~/Documents/metarace/venv/bin/pip3 install ./metarace_roadmeet-1.13.2-py3-none-any.whl

Create a new empty roadmeet:

	$ ~/Documents/metarace/venv/bin/roadmeet


### Run From Source Tree

Activate the virtual env, optionally install
any required libraries, clone the repository
and run roadmeet directly:

	$ source ~/Documents/metarace/venv/bin/activate
	(venv) $ pip3 install metarace
	(venv) $ git clone https://github.com/ndf-zz/metarace-roadmeet.git
	(venv) $ cd metarace-roadmeet/src
	(venv) $ python3 -m roadmeet


## Post-Installation Notes

Run roadmeet once to initialise a metarace shared configuration:

	$ ~/Documents/metarace/venv/bin/roadmeet

Optionally configure defaults for new meets and library options:

	$ ~/Documents/metarace/venv/bin/roadmeet --edit-default


### Gnome Desktop

By default, Gnome uses a system font which does not have
fixed-width digits. As a result, rolling times displayed
in roadmeet will jiggle left and right as the digits change,
and right-aligned time columns will not align correctly
at the decimal point.

To correct this, install gnome-tweaks and change the
system font to one with fixed-width digits eg:
Noto Sans Regular.

Debugging messages can be viewed using journactl:

	$ journalctl -f

### XFCE

The XFCE default window manager uses function keys to switch
workspaces, rendering them inaccessible to roadmeet.
To use these function keys in roadmeet (eg for
reports, arming and reset), first clear the relevant
window manager shortcuts.

Under Settings, Window Manager, Keyboard, locate the
"Workspace N" entries and clear the shortcut for each one by
selecting the "Clear" button.

Roadmeet can be configured to open meet folders in Thunar
by creating a custom action with appearance conditions 
set to include "Directories". The action can then be
added to the toolbar or triggered from a context menu.

Following an automated install, you may need to log out
and back in for the menu entries to be properly updated.

Debugging messages are appended to ~/.xsession-errors,
view with tail:

	$ tail -f ~/.xsession-errors


## System-Specific Preparation

### Debian 11+, Ubuntu, Mint, MX (apt)

Install system requirements for roadmeet and metarace with apt:

	$ sudo apt install python3-venv python3-pip
	$ sudo apt install python3-cairo python3-gi python3-gi-cairo
	$ sudo apt install gir1.2-gtk-3.0 gir1.2-rsvg-2.0 gir1.2-pango-1.0
	$ sudo apt install python3-serial python3-paho-mqtt python3-dateutil python3-xlwt

Optionally add fonts, PDF viewer, rsync and MQTT broker:

	$ sudo apt install fonts-texgyre fonts-noto evince rsync mosquitto

Add your user to the group **dialout**
in order to access serial ports:

	$ sudo gpasswd -a "$USER" dialout


### Arch, Manjaro, EndeavourOS (pacman)

Install system requirements with pacman:

	$ sudo pacman -S --needed python python-pip gtk3
	$ sudo pacman -S --needed python-pyserial python-dateutil python-xlwt python-paho-mqtt python-gobject python-cairo

Optionally install pdf viewer, fonts, rsync and mqtt broker:

	$ sudo pacman -S --needed noto-fonts tex-gyre-fonts evince rsync mosquitto
	$ sudo systemctl enable mosquitto.service

Add your user to the group **uucp**
in order to access serial ports:

	$ sudo gpasswd -a "$USER" uucp


### Gentoo Linux

Install required system libraries, or select a
suitable meta-package (eg XFCE):

	# emerge --ask -n xfce-base/xfce4-meta x11-themes/gnome-themes-standard

Install required python packages:

	# emerge --ask -n dev-libs/gobject-introspection dev-python/pygobject dev-python/python-dateutil dev-python/xlwt dev-python/pyserial dev-python/paho-mqtt

Install optional fonts, pdf viewer and MQTT broker:

	# emerge --ask -n media-fonts/tex-gyre media-fonts/noto app-text/evince app-misc/mosquitto net-misc/rsync

Add your user to the group **dialout**
in order to access serial ports:

	# gpasswd -a "username" dialout


### Alpine Linux (apk)

Setup a desktop environment, then add python requirements
with apk:

	# apk add py3-pip py3-pyserial py3-dateutil py3-paho-mqtt py3-gobject3 py3-cairo

Install optional fonts, pdf viewer, rsync and MQTT broker:

	# apk add font-noto evince rsync mosquitto

Install Tex Gyre fonts from Gust:

	$ wget https://www.gust.org.pl/projects/e-foundry/tex-gyre/whole/tg2_501otf.zip
	$ mkdir -p ~/.local/share/fonts
	$ unzip -j -d ~/.local/share/fonts tg2_501otf.zip
	$ fc-cache -f

Add your user to the group **dialout**
in order to access serial ports:

	$ sudo gpasswd -a "$USER" dialout


### Fedora Linux (dnf)

Install system requirements:

	$ sudo dnf install gtk3 gobject-introspection cairo-gobject
	$ sudo dnf install python3-pip python3-cairo
	$ sudo dnf install python3-pyserial python3-paho-mqtt python3-dateutil python-xlwt

Optionally add fonts, PDF viewer, rsync and MQTT broker:

	$ sudo dnf install google-noto-sans-fonts google-noto-mono-fonts google-noto-emoji-fonts texlive-tex-gyre evince rsync mosquitto
	$ sudo systemctl enable mosquitto.service

Add your user to the group **dialout**
in order to access serial ports:

	$ sudo gpasswd -a "$USER" dialout


### Slackware

Install a desktop environment (eg XFCE),
python packages will be installed
as required by pip.

Note: Slackware does not ship evince with the XFCE
desktop, but sets it as the Gtk print preview application.
To enable print preview, install evince from slackbuilds,
or add an entry in ~/.config/gtk-3.0/settings.ini
to point to another application:

	[Settings]
	gtk-print-preview-command=xpdf %f

Install Tex Gyre fonts from Gust:

	$ wget https://www.gust.org.pl/projects/e-foundry/tex-gyre/whole/tg2_501otf.zip
	$ mkdir -p ~/.local/share/fonts
	$ unzip -j -d ~/.local/share/fonts tg2_501otf.zip
	$ fc-cache -f

Add your user to the group **dialout**
in order to access serial ports:

	$ sudo gpasswd -a "$USER" dialout


### FreeBSD

Install a desktop environment (eg XFCE), then
install optional components with pkg:

	# pkg install evince rsync mosquitto

Add user to group **dialer** in order to
access serial ports:

	# pw group mod -n dialer -m op

Install Tex Gyre fonts from Gust:

	$ wget https://www.gust.org.pl/projects/e-foundry/tex-gyre/whole/tg2_501otf.zip
	$ mkdir -p ~/.local/share/fonts
	$ unzip -j -d ~/.local/share/fonts tg2_501otf.zip
	$ fc-cache -f

Note: Use callout serial devices for decoder access. For example,
a race result active decoder on the first USB serial port:

	rru:/dev/cuaU0


### MacOS / Brew

*Untested*

Install system requirements:

	$ brew install python@3.11 gtk+3 librsvg pygobject3

Add optional pdf viewer and mqtt broker:

	$ brew install evince rsync mosquitto

Install Tex Gyre fonts from Gust:

	$ wget https://www.gust.org.pl/projects/e-foundry/tex-gyre/whole/tg2_501otf.zip
	$ mkdir -p ~/.local/share/fonts
	$ unzip -j -d ~/.local/share/fonts tg2_501otf.zip
	$ fc-cache -f


### Windows / MSYS2 (pacman)

> *Note*: Instructions below will yield a working roadmeet
> with MSYS2, however, Windows users unwilling to run
> a dedicated host system may have a better experience
> running roadmeet from an emulator loaded with a
> well-supported POSIX system like Debian Gnu/Linux or FreeBSD.

Download and install MSYS2 from [msys2.org](https://www.msys2.org).

From the mingw64 environment, install gtk and python libraries
with pacman (the following assumes x86_64 as the target):

	$ pacman -S --needed mingw-w64-x86_64-gtk3 mingw-w64-x86_64-gobject-introspection
	$ pacman -S --needed mingw-w64-x86_64-python-pip mingw-w64-x86_64-python-gobject
	$ pacman -S --needed mingw-w64-x86_64-python-dateutil mingw-w64-x86_64-python-pyserial mingw-w64-x86_64-python-xlwt

Then use pip to install roadmeet:

	$ pip3 install metarace-roadmeet

Optionally install Tex Gyre Fonts to the host:

   - Download OTF fonts from [gust.org.pl](https://www.gust.org.pl/projects/e-foundry/tex-gyre/whole/tg2_501otf.zip)
   - Extract and install fonts using Explorer

Optionally install mosquitto to the mingw64 environment:

	$ pacman -S mingw-w64-x86_64-mosquitto

Roadmeet can be started from the .exe installed to the MINGW64
bin folder, likely C:\msys64\mingw64\bin\roadmeet.exe. Mosquitto
will need to be launched for telegraph connections to function.
