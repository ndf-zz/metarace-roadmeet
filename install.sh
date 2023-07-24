#!/usr/bin/env sh
#
# Crude user installation script for Linux/GNU systems
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
  echo Incorrect operating system.
  exit
fi

# check distribution
pkgstyle=apt
if [ -e /etc/os-release ] ; then
  . /etc/os-release
  echo -n "Distribution: "
  dv=`echo "$VERSION_ID" | cut -d . -f 1`
  case "x$ID" in
    "xdebian")
      if [ "$dv" -gt 10 ] ; then
        echo "$NAME $VERSION"
      else
        echo "$NAME version $VERSION not supported."
        exit
      fi
    ;;
    "xubuntu")
      if [ "$dv" -gt 21 ] ; then
        echo "$NAME $VERSION"
      else
        echo "$NAME version $VERSION not supported."
        exit
      fi
    ;;
    "xarch")
      pkgstyle=pacman
      echo "Arch [TODO]"
      exit
    ;;
    "xalpine")
      pkgstyle=apk
      echo "Alpine [TODO]"
      exit
    ;;
    *)
      echo "$ID not supported by this installer."
      exit
    ;;
  esac
else
  echo OS info not found.
  exit
fi

# check python version
echo -n "Python >= 3.9: "
if python3 -c 'import sys
print(sys.version_info>=(3,9))' | grep -F "True" ; then
  true
else
  echo Python version too old.
  exit
fi

# check deb sys packages and group membership
gchange=n
read -p "Update system packages? [y/N]: " doupdate
if [ "x$doupdate" = "xy" ] ; then
  echo -n "Updating system packages: "
  sudo apt-get update -q
  sudo apt-get install -q -y python3-venv python3-pip python3-cairo python3-gi python3-gi-cairo python3-serial python3-paho-mqtt python3-dateutil python3-xlwt fonts-texgyre fonts-noto evince gir1.2-gtk-3.0 gir1.2-rsvg-2.0 gir1.2-pango-1.0 mosquitto
  echo Done.
  # Make sure user is member of dialout group
  echo -n "Serial port access: "
  if groups | grep -F dialout >/dev/null 2>&1 ; then
    echo OK.
  else
    sudo gpasswd -a "$USER" dialout
    gchange=y
  fi
else
  echo System packages not updated.
fi

# check for venv module
echo -n "Python venv: "
if python3 -c 'import venv' >/dev/null 2>&1 ; then
  echo OK.
else
  echo Not installed.
  exit
fi

# check working dir
DPATH="$HOME/Documents/metarace"
VDIR="venv"
VPATH="$DPATH/$VDIR"
echo -n "Venv: "
if [ -d "$VPATH" ] ; then
  echo "$VDIR"
else
  echo Build new.
  mkdir -p "$DPATH"
fi

# re-build venv
echo -n "Updating venv: "
python3 -m venv --system-site-packages "$VPATH"
echo Done.

# install packages
echo -n "Updating roadmeet: "
if [ -e "$VPATH/bin/pip3" ] ; then 
  "$VPATH/bin/pip3" -q install metarace-roadmeet --upgrade
  echo "$VPATH/bin/roadmeet"
else
  echo Unable to install: Virtual env not setup.
fi

# run a dummy metarace init to populate the data directories
echo -n "Defaults folder: "
DEFS="$DPATH/default/metarace_icon.svg"
if [ -e "$DEFS" ] ; then
  echo Unchanged.
else
  "$VPATH/bin/python3" -c 'import metarace
metarace.init()'
  echo Updated.
fi

echo -n "Desktop entry: "
XDGPATH="$HOME/.local/share/applications"
SPATH="$XDGPATH/metarace"
mkdir -p "$SPATH"
TMPF=`mktemp -p "$SPATH"`
tee "$TMPF" <<__EOF__ >/dev/null
[Desktop Entry]
Version=1.0
Type=Application
Exec=$VPATH/bin/roadmeet %f
Icon=$DPATH/default/metarace_icon.svg
Terminal=false
StartupNotify=true
MimeType=inode/directory;application/json;
Name=Roadmeet
Comment=Timing and results for road cycling meets
Categories=Utility;GTK;Sports;
__EOF__
mv "$TMPF" "$SPATH/roadmeet.desktop"
echo "$SPATH/roadmeet.desktop"
echo -n "Config entry: "
TMPF=`mktemp -p "$SPATH"`
tee "$TMPF" <<__EOF__ >/dev/null
[Desktop Entry]
Version=1.0
Type=Application
Exec=$VPATH/bin/roadmeet --edit-default
Icon=$DPATH/default/metarace_icon.svg
Terminal=false
StartupNotify=true
Name=Roadmeet Config
Comment=Edit roadmeet default configuration
Categories=Settings;
__EOF__
mv "$TMPF" "$SPATH/roadmeet-config.desktop"
echo "$SPATH/roadmeet-config.desktop"
echo -n "Update MIME types cache: "
update-desktop-database -q "$XDGPATH"
echo "Done."

echo
echo Package roadmeet installed.
if [ "x$gchange" = "xy" ] ; then
  echo "Group membership changed, log out and back in for serial port access"
fi
