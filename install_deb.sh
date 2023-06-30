#!/usr/bin/env sh
#
# Crude installation script for Debian/Linux/GNU systems
#
set -e

# abort if not normal user
if [ "$(id -u)" -eq 0 ]; then
  echo Running as root: Aborting installation.
  exit
fi

# check sys
echo -n "OS: "
if uname -o | grep -F "GNU/Linux" ; then
  true
else
  echo Incorrect Operating System.
  exit
fi

# check distribution
echo -n "Debian: "
if [ -e /etc/debian_version ] ; then
  cat /etc/debian_version
else
  echo Not found.
  exit
fi

# check python version
echo -n "Python >= 3.9: "
if python3 -c 'import sys
print(sys.version_info >= (3,9))' | grep -F "True" ; then
  true
else
  echo Python version too old.
  exit
fi

# check deb sys packages
read -p "Update system packages? [y/N]: " doupdate
if [ "x$doupdate" = "xy" ] ; then
  echo -n "Updating system packages: "
  sudo apt-get update -q
  sudo apt-get install -q -y python3-venv python3-pip python3-cairo python3-gi python3-gi-cairo python3-serial python3-paho-mqtt python3-dateutil python3-xlwt tex-gyre fonts-noto evince gir1.2-gtk-3.0 gir1.2-rsvg-2.0 gir1.2-pango-1.0
  echo Done.
else
  echo System packages not updated.
fi

# check working dir
DPATH="$HOME/Documents/metarace"
VDIR="venv"
VPATH="$DPATH/$VDIR"
echo -n "Venv: "
if [ -d $VPATH ] ; then
  echo $VPATH
else
  echo Build new.
  mkdir -p $DPATH
fi

# re-build venv
echo -n "Updating venv: "
python3 -m venv --system-site-packages $VPATH
echo Done.

# install packages
echo -n "Updating roadmeet: "
if [ -e $VPATH/bin/pip3 ] ; then 
  $VPATH/bin/pip3 -q install metarace-roadmeet --upgrade
  echo $VPATH/bin/roadmeet
else
  echo Unable to install: Virtual env not setup.
fi

# install desktop shortcut entry
echo -n "Desktop entry: "
SPATH="$HOME/.local/share/applications/metarace"
mkdir -p $SPATH
if [ -e $SPATH/roadmeet.desktop ] ; then
  echo Unchanged.
else
  TMPF=`mktemp -p $SPATH`
  tee $TMPF <<__EOF__ >/dev/null
[Desktop Entry]
Version=1.0
Type=Application
Exec=$VPATH/bin/roadmeet
Icon=$DPATH/default/metarace_icon.svg
Terminal=false
StartupNotify=true
Name=Roadmeet
Comment=Timing and results for road cycling meets
Categories=Utility;GTK;Sports;
__EOF__
  mv $TMPF $SPATH/roadmeet.desktop
  echo $SPATH/roadmeet.desktop
fi
echo
echo Package roadmeet installed.
