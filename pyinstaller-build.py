import os
import sys
import zipfile
import PyInstaller.__main__

EXE_NAME = 'CookpadLiveDown'
VERSION = "1.3.0"
ADDITIONAL_FILES = [
    # 'readme.txt',
    'downloader_option.json',
    'changelog.txt',
]
ADDITIONAL_SOURCE = [
    # 'requirements.txt',
]
ADDITIONAL_FILES_ROOT = [
    'readme.txt',
]
BLACKLIST_DIRS = ['venv', 'build', 'dist', '.git', '.idea', '__pycache__']
ONE_FILE = False

PyInstaller.__main__.run([
    '-y',
    f'--name={EXE_NAME}',
    '--onefile' if ONE_FILE else '--onedir',
    # '--noupx',
    '--console',
    # '--windowed',
    '--add-binary', 'ffmpeg.exe' + os.pathsep + 'ffmpeg',
    'cui_main.py'
])

with zipfile.ZipFile(f'dist/{EXE_NAME}-{VERSION}.zip', 'w',
                     compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    print('Main exe')
    if ONE_FILE:
        zf.write(f'dist/{EXE_NAME}.exe', f'{EXE_NAME}.exe')
    else:
        executable_root = os.path.abspath(os.path.join('dist', EXE_NAME))
        for root, dirs, files in os.walk(executable_root):
            for name in files:
                fullname = os.path.join(root, name)
                zip_name = fullname.replace(executable_root, 'bin', 1)
                print('  ' + zip_name)
                zf.write(fullname, zip_name)
    print('Additional files')
    for fn in ADDITIONAL_FILES:
        print('  ' + fn)
        dest_file = fn if ONE_FILE else os.path.join('bin', fn)
        zf.write(fn, os.path.join('bin', fn))
    print('Additional files (root)')
    for fn in ADDITIONAL_FILES_ROOT:
        print('  ' + fn)
        zf.write(fn, fn)
    print('Source files')
    for root, dirs, files in os.walk('.'):
        for name in files:
            ext_list = name.rsplit('.', 1)
            ext = '' if len(ext_list) == 1 else ext_list[1]
            if ext not in ['py', 'txt']:
                continue
            fullname = os.path.join(root, name)
            print('  ' + fullname)
            zf.write(fullname, f'src/{fullname}')
        for d in dirs[:]:
            if d.lower() in BLACKLIST_DIRS:
                dirs.remove(d)
    print('Additional source files')
    for fn in ADDITIONAL_SOURCE:
        print('  ' + fn)
        zf.write(fn, f'src/{fn}')
print('done!')
sys.exit(0)
