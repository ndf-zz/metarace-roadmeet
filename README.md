# metarace-roadmeet

Timing and result application for UCI Part 2 Road Races,
UCI Part 5 Cyclo-Cross, criterium, road handicap and
ad-hoc time trial events.

![roadmeet screenshot](screenshot.png "roadmeet")


## Usage

Choose meet folder and open:

	$ roadmeet

Open an existing road meet:

	$ roadmeet PATH

Create empty meet folder and open it:

	$ roadmeet --create

Edit default configuration:

	$ roadmeet --edit-default


## Support

   - Signal Group: [metarace](https://signal.group/#CjQKII2j2E7Zxn7dHgsazfKlrIXfhjgZOUB3OUFhzKyb-p_bEhBehsI65MhGABZaJeJ-tMZl)
   - Github Issues: [issues](https://github.com/ndf-zz/metarace-roadmeet/issues)


## Requirements

   - Python >= 3.11
   - PyGObject
   - Gtk >= 3.22
   - metarace >= 2.1.10
   - tex-gyre fonts (optional, recommended)
   - evince (optional, recommended)
   - rsync (optional)
   - mosquitto (optional)


## Automated Installation

For semi-automated installation on common unix-like
desktop systems, download the metarace install script
and run with sh:

	$ wget https://github.com/ndf-zz/metarace/raw/refs/heads/master/metarace-install.sh
	$ sh metarace-install.sh

For installation on Windows systems, a powershell script
is provided to install metarace applications under a
WSL Debian container:

	wget https://github.com/ndf-zz/metarace/raw/refs/heads/master/wsl-install.ps1


## Post-Installation Notes

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

	$ ~/Documents/metarace/venv/bin/roadmeet --create


### Install From Wheel

Download the roadmeet wheel from github and verify signature:

	$ wget https://github.com/ndf-zz/metarace-roadmeet/releases/download/v1.13.8/metarace_roadmeet-1.13.8-py3-none-any.whl
	$ wget https://github.com/ndf-zz/metarace-roadmeet/releases/download/v1.13.8/metarace_roadmeet-1.13.8-py3-none-any.whl.asc
	$ gpg --verify metarace_roadmeet-1.13.8-py3-none-any.whl.asc

Use pip in your virtual env to install the roadmeet wheel:

	$ ~/Documents/metarace/venv/bin/pip3 install ./metarace_roadmeet-1.13.8-py3-none-any.whl

Create a new empty roadmeet:

	$ ~/Documents/metarace/venv/bin/roadmeet --create


### Run From Source Tree

Activate the virtual env, optionally install
any required libraries, clone the repository
and run roadmeet directly:

	$ source ~/Documents/metarace/venv/bin/activate
	(venv) $ pip3 install metarace
	(venv) $ git clone https://github.com/ndf-zz/metarace-roadmeet.git
	(venv) $ cd metarace-roadmeet/src
	(venv) $ python3 -m roadmeet


## System-Specific Preparation Notes

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

[TBC]


### Windows / WSL

Install VirtualMachinePlatform and Windows Subsystem for Linux
with Powershell:

	Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
	wsl --install --no-distribution

Reboot computer, then prepare a Debian container:

	wsl --install -d Debian

In the Debian shell, install wget, cups and thunar:

	$ sudo apt-get install wget cups thunar

Add your user to the lpadmin group:

	$ sudo gpasswd -a "$USER" "lpadmin

Download the installer and run with sh:

	$ wget https://github.com/ndf-zz/metarace/raw/refs/heads/master/metarace-install.sh
	$ sh metarace-install.sh

