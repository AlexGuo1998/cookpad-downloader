import io
import json
import os
import shutil
import time
import traceback
from getpass import getpass
import re
import subprocess

from tkinter import Tk  # from tkinter import Tk for Python 3.x
import tkinter.filedialog
import m3u8

import cookpad_constants
import login_manager
import download
from query_input import query_input
from find_ffmpeg import find_ffmpeg

try:
    g_manager = login_manager.get_manager()
except FileNotFoundError:
    g_manager = login_manager.LoginManager()

g_downloader_options = download.get_downloader_options()


def _get_episode_title(j):
    episode_id = j['episode']['id']
    program = j['episode']['program']['title']
    part = j['episode']['part']
    title = j['episode']['title']
    full_title = f'{program} #{part} ({episode_id}) {title}'
    return full_title


def login_manage():
    global g_manager
    while True:
        print('Getting current login info, please wait...')
        try:
            login_ok, login_info = g_manager.check(access_web=True)
        except Exception as e:
            login_ok, login_info = False, None
        if login_ok:
            j = login_info.json()
            is_gold = 'Yes' if j['gold_subscriber'] else 'No'
            nickname = j['user_profile']['name'] or 'No name'
            login_info = f'\nName: {nickname}, Gold subscriber: {is_gold}'
        else:
            login_info = 'Not logged in'
        r = query_input(
            f'========== Login / Logout / Status ==========\n'
            f'Current login: {login_info}\n'
            f'1. Login\n'
            f'2. Logout\n'
            f'Q. Back',
            lambda x: x in '12q',
            '[12Q]? '
        )
        if r == '1':
            if g_manager.username and g_manager.password:
                use_saved = query_input('Use saved username + password?',
                                        lambda x: x in 'yn', '[YN]? ') == 'y'
            else:
                use_saved = False
            username = g_manager.username
            password = g_manager.password
            save_password = True
            if not use_saved:
                if username:
                    print(f'Saved username: {username}\n'
                          f'Press Enter to use this directly')
                username = input('Email: ') or username
                password = getpass('Password: ')
                save_password = query_input(
                    'Save password? (This is usually not needed)',
                    lambda x: x in 'yn', '[YN]? ', 'N') == 'y'
            try:
                g_manager.login(username, password, save_password)
            except Exception as e:
                traceback.print_exc()
        elif r == '2':
            cleanup = query_input(
                'Cleanup saved username / password?',
                lambda x: x in 'yn', '[YN]? ', 'N') == 'y'
            try:
                g_manager.logout(cleanup)
            except Exception as e:
                traceback.print_exc()
        elif r == 'q':
            return
        login_manager.save_manager(g_manager)


def download_json():
    global g_manager

    def valid_url(url):
        if url.lower() == 'q':
            return 'q'
        try:
            episode = int(url)
            return episode
        except ValueError:
            m = re.match(r'https://www\.cookpad\.tv/episodes/(\d+)', url)
            if m is not None:
                episode = int(m.group(1))
                return episode
            print(
                'Invalid URL! Please check and retry.\n'
                'Should be like: https://www.cookpad.tv/episodes/1234'
            )
            return False

    print('========== Download Episode JSON ==========')
    episode = query_input(
        'Paste episode url or episode id, Q to cancel:\n'
        '(Should be like: https://www.cookpad.tv/episodes/1234)',
        valid_url, lower_case=False)
    if episode == 'q':
        print('Canceled!')
        time.sleep(1)
        return

    print(f'Getting info for episode {episode}')
    r = g_manager.api_get(f'/api/v2/episode_details/{episode}', {
        'geometry[episode][width]': 640,  # image size (px)
        'geometry[teacher][width]': 640,
        'geometry[recipe][width]': 640,
        'fields': cookpad_constants.fields['EpisodeDetailEntity'],
    })
    _ = r.content  # pre-fetch content
    j = r.json()

    full_title = _get_episode_title(j)
    print(f'Episode title:\n{full_title}')

    print('Input filename to save\nPress Enter to bring up file browser')
    filename = input('? ')
    if not filename:
        Tk().withdraw()
        filename = tkinter.filedialog.asksaveasfilename(
            initialfile=full_title,
            filetypes=[('JSON', '*.json'), ('All files', '*.*')], defaultextension='.json')
    if not filename:
        print('Canceled!')
        time.sleep(1)
        return
    filename = os.path.abspath(filename)
    print(f'Saving to "{filename}"')
    with open(filename, 'wb') as f:
        f.write(r.content)
    print('Done!')
    time.sleep(1)


def create_project():
    global g_downloader_options
    print('========== Create Project Folder from JSON ==========')
    print('Input episode JSON file\nPress Enter to bring up file browser')
    input_json = input('? ')
    if not input_json:
        Tk().withdraw()
        input_json = tkinter.filedialog.askopenfilename(
            filetypes=[('JSON', '*.json'), ('All files', '*.*')], defaultextension='.json')
    if not input_json:
        print('Canceled!')
        time.sleep(1)
        return
    input_json = os.path.abspath(input_json)
    with open(input_json, 'rb') as f:
        j = json.load(f)
    full_title = _get_episode_title(j)
    print(f'Episode title:\n{full_title}')

    print('Available streams:')
    streams = j['episode']['archive_streamings']
    for i, stream in enumerate(streams, start=1):
        name = stream['name']
        w, h = stream['max_width'], stream['max_height']
        print(f'{i}: {name} ({w}x{h})')
    stream_id = query_input('Download which stream?\n'
                            'Q to cancel',
                            lambda x: (x.isdigit() and 0 < int(x) <= len(streams)) or x == 'q')
    if stream_id == 'q':
        print('Canceled!')
        time.sleep(1)
        return
    stream_id = int(stream_id)
    stream = streams[stream_id - 1]
    stream_url = stream['streaming_url']

    print('Checking stream url, please wait...')
    base_url, m3u8_filename = stream_url.rsplit('/', maxsplit=1)
    with io.BytesIO() as m3u8_file:
        dl = download.SingleDownloader(stream_url, m3u8_file, g_downloader_options)
        dl.start()
        assert dl.status() == download.DownloadStatus.DONE
        m3u8_content = m3u8_file.getbuffer().tobytes()
    m3u8_obj = m3u8.loads(m3u8_content.decode('utf-8'), stream_url)
    assert m3u8_obj.is_variant, 'should be variant'

    best_quality_id = 0
    best_quality_br = 0
    print('Available variants (quality):')
    for i, playlist in enumerate(m3u8_obj.playlists, start=1):
        w, h = playlist.stream_info.resolution
        br = playlist.stream_info.average_bandwidth
        print(f'{i}: {w}x{h} {br // 1024}kbps ({(br * 60) // (1024 * 1024 * 8)}MB per minute)')
        if br > best_quality_br:
            best_quality_id = i
            best_quality_br = br
    variant_id = query_input(
        'Download which variant?\n'
        'Q to cancel',
        lambda x: (x.isdigit() and 0 < int(x) <= len(m3u8_obj.playlists)) or x == 'q',
        default_input=str(best_quality_id))
    if variant_id == 'q':
        print('Canceled!')
        time.sleep(1)
        return
    variant_id = int(variant_id)
    variant_url = m3u8_obj.playlists[variant_id - 1].absolute_uri

    print('Checking variant_url, please wait...')
    base_url, m3u8_variant_filename = variant_url.rsplit('/', maxsplit=1)
    with io.BytesIO() as m3u8_file:
        dl = download.SingleDownloader(variant_url, m3u8_file, g_downloader_options)
        dl.start()
        assert dl.status() == download.DownloadStatus.DONE
        m3u8_variant_content = m3u8_file.getbuffer().tobytes()
    m3u8_obj = m3u8.loads(m3u8_variant_content.decode('utf-8'), variant_url)

    # patch m3u8 file
    download_list = []
    for i, seg in enumerate(m3u8_obj.segments, start=1):
        seg_url = seg.absolute_uri
        base_url, seg_fn = seg_url.rsplit('/', maxsplit=1)
        seg.uri = seg_fn
        download_list.append((seg_url, seg_fn, f'{i}.ts'))

    start_offset = j['episode']['archive_start_offset']

    while True:
        print('Input a empty dir to save project\nPress Enter to bring up file browser')
        dirname = input('? ')
        if not dirname:
            path, file = os.path.split(input_json)
            Tk().withdraw()
            dirname = tkinter.filedialog.askdirectory(
                initialdir=path, mustexist=False)
        if not dirname:
            print('Canceled!')
            time.sleep(1)
            return
        dirname = os.path.abspath(dirname)
        os.makedirs(dirname, exist_ok=True)
        files = os.listdir(dirname)
        if files:
            do_delete = query_input(
                'There are files in this directory, empty it?\n'
                f'Directory: {dirname}\n'
                f'Files: {files}\n'
                'Choose N to select a new directory',
                lambda x: x in 'yn',
                '[YN]? ', 'N') == 'y'
            if do_delete:
                shutil.rmtree(dirname)
                os.makedirs(dirname, exist_ok=True)
                break
        else:
            break

    print(f'Saving to "{dirname}"')
    project = {
        'info_json': 'episode_detail.json',
        'stream_id': stream_id,
        'stream_url': stream_url,
        'variant_id': variant_id,
        'variant_url': variant_url,
        'start_offset': start_offset,
        'playlist_patched': 'patched.m3u8',
        'download_list': download_list,
    }
    shutil.copy(input_json, os.path.join(dirname, 'episode_detail.json'))
    with open(os.path.join(dirname, m3u8_filename), 'wb') as f:
        f.write(m3u8_content)
    with open(os.path.join(dirname, m3u8_variant_filename), 'wb') as f:
        f.write(m3u8_variant_content)
    m3u8_obj.dump(os.path.join(dirname, 'patched.m3u8'))
    with open(os.path.join(dirname, 'project.json'), 'w', encoding='utf-8') as f:
        json.dump(project, f)
    print('Done!')
    time.sleep(1)


def create_project_from_m3u8():
    global g_downloader_options

    def valid_url(url):
        if url.lower() == 'q':
            return 'q'
        m = re.match(r'^https?://.*\.m3u8(?:\?.+)?$', url)
        if m is not None:
            return url
        print('Invalid URL! Please check and retry.')
        return False

    print('========== Create Project Folder from m3u8 playlist ==========')
    stream_url = query_input(
        'Paste .m3u8 file url, Q to cancel:',
        valid_url, lower_case=False)
    if stream_url == 'q':
        print('Canceled!')
        time.sleep(1)
        return

    print('Checking stream url, please wait...')
    base_url, m3u8_filename = stream_url.rsplit('/', maxsplit=1)
    with io.BytesIO() as m3u8_file:
        dl = download.SingleDownloader(stream_url, m3u8_file, g_downloader_options)
        dl.start()
        assert dl.status() == download.DownloadStatus.DONE
        m3u8_content = m3u8_file.getbuffer().tobytes()
    m3u8_obj = m3u8.loads(m3u8_content.decode('utf-8'), stream_url)
    if m3u8_obj.is_variant:
        best_quality_id = 0
        best_quality_br = 0
        print('Available variants (quality):')
        for i, playlist in enumerate(m3u8_obj.playlists, start=1):
            w, h = playlist.stream_info.resolution or (0, 0)
            br = playlist.stream_info.average_bandwidth or playlist.stream_info.bandwidth or 0
            print(f'{i}: {w}x{h} {br // 1024}kbps ({(br * 60) // (1024 * 1024 * 8)}MB per minute)')
            if br > best_quality_br:
                best_quality_id = i
                best_quality_br = br
        variant_id = query_input(
            'Download which variant?\n'
            'Q to cancel',
            lambda x: (x.isdigit() and 0 < int(x) <= len(m3u8_obj.playlists)) or x == 'q',
            default_input=str(best_quality_id))
        if variant_id == 'q':
            print('Canceled!')
            time.sleep(1)
            return
        variant_id = int(variant_id)
        variant_url = m3u8_obj.playlists[variant_id - 1].absolute_uri

        print('Checking variant_url, please wait...')
        base_url, m3u8_variant_filename = variant_url.rsplit('/', maxsplit=1)
        with io.BytesIO() as m3u8_file:
            dl = download.SingleDownloader(variant_url, m3u8_file, g_downloader_options)
            dl.start()
            assert dl.status() == download.DownloadStatus.DONE
            m3u8_variant_content = m3u8_file.getbuffer().tobytes()
        m3u8_obj = m3u8.loads(m3u8_variant_content.decode('utf-8'), variant_url)
    else:
        variant_id = -1
        variant_url = stream_url
        m3u8_variant_content = m3u8_content
        m3u8_variant_filename = m3u8_filename

    # patch m3u8 file
    download_list = []
    for i, seg in enumerate(m3u8_obj.segments, start=1):
        seg_url = seg.absolute_uri
        base_url, seg_fn = seg_url.rsplit('/', maxsplit=1)
        if '?' in seg_fn:
            # strip args
            seg_fn, _ = seg_fn.split('?', maxsplit=1)
        seg.uri = seg_fn
        download_list.append((seg_url, seg_fn, f'{i}.ts'))

    while True:
        print('Input a empty dir to save project\nPress Enter to bring up file browser')
        dirname = input('? ')
        if not dirname:
            Tk().withdraw()
            dirname = tkinter.filedialog.askdirectory(
                mustexist=False)
        if not dirname:
            print('Canceled!')
            time.sleep(1)
            return
        dirname = os.path.abspath(dirname)
        os.makedirs(dirname, exist_ok=True)
        files = os.listdir(dirname)
        if files:
            do_delete = query_input(
                'There are files in this directory, empty it?\n'
                f'Directory: {dirname}\n'
                f'Files: {files}\n'
                'Choose N to select a new directory',
                lambda x: x in 'yn',
                '[YN]? ', 'N') == 'y'
            if do_delete:
                shutil.rmtree(dirname)
                os.makedirs(dirname, exist_ok=True)
                break
        else:
            break

    print(f'Saving to "{dirname}"')
    project = {
        'info_json': '?',
        'stream_id': '?',
        'stream_url': stream_url,
        'variant_id': variant_id,
        'variant_url': variant_url,
        'start_offset': 0,
        'playlist_patched': 'patched.m3u8',
        'download_list': download_list,
    }
    # strip args
    if '?' in m3u8_filename:
        m3u8_filename, _ = m3u8_filename.split('?', maxsplit=1)
    if '?' in m3u8_variant_filename:
        m3u8_variant_filename, _ = m3u8_variant_filename.split('?', maxsplit=1)

    with open(os.path.join(dirname, m3u8_filename), 'wb') as f:
        f.write(m3u8_content)
    with open(os.path.join(dirname, m3u8_variant_filename), 'wb') as f:
        f.write(m3u8_variant_content)
    m3u8_obj.dump(os.path.join(dirname, 'patched.m3u8'))
    with open(os.path.join(dirname, 'project.json'), 'w', encoding='utf-8') as f:
        json.dump(project, f)
    print('Done!')
    time.sleep(1)


def process_project():
    global g_downloader_options
    print('Input project dir\nPress Enter to bring up file browser')
    dirname = input('? ')
    if not dirname:
        Tk().withdraw()
        dirname = tkinter.filedialog.askdirectory(mustexist=True)
    if not dirname:
        print('Canceled!')
        time.sleep(1)
        return
    dirname = os.path.abspath(dirname)

    with open(os.path.join(dirname, 'project.json'), 'rb') as f:
        project = json.load(f)

    while True:
        r = query_input(
            f'========== Process Project ==========\n'
            f'Current project: {dirname}\n'
            f'1. Download all files\n'
            f'2. Tweak downloader options\n'
            f'3. Reset downloader options\n'
            f'4. To MP4 file\n'
            f'Q. Back',
            lambda x: x in '1234q',
            '[1234Q]? '
        )
        try:
            if r == '1':
                queue = project['download_list']
                queue_new = []
                for url, filename, info in queue:
                    filename_full = os.path.join(dirname, filename)
                    if os.access(filename_full, os.F_OK):
                        continue
                    queue_new.append((url, filename_full, info))
                if len(queue_new) == 0:
                    print('Nothing to download!')
                    time.sleep(1)
                    continue
                dq = download.DownloadQueue(queue_new, g_downloader_options)
                try:
                    dq.run()
                finally:
                    files = os.listdir(dirname)
                    for file in files:
                        if file.endswith(g_downloader_options.temp_suffix):
                            os.remove(os.path.join(dirname, file))
                if len(dq.results) == 0:
                    print('Download failed due to severe error!')
                    time.sleep(1)
                    continue
                print('Download finished!')
                error_count = 0
                for (url, filename, info), (success, message) in zip(queue_new, dq.results):
                    if not success:
                        error_count += 1
                        print(f'Error when downloading {filename} ({url}):\n{message}\n')
                if error_count > 0:
                    print(f'{error_count} errors encountered')
                    if error_count == len(queue_new):
                        print('All files failed, try again or contact author!')
                else:
                    print('All files done')
            elif r == '2':
                print('Please edit "downloader_option.json" with a text editor')
                input('Press Enter to load...')
                g_downloader_options = download.get_downloader_options(show_exceptions=True)
            elif r == '3':
                download.save_downloader_options(download.DownloaderOptions())
                print('Reset "downloader_option.json" done')
            elif r == '4':
                print('Checking...')
                files = os.listdir(dirname)
                missing = []
                for url, filename, info in project['download_list']:
                    if filename not in files:
                        missing.append(filename)
                if missing:
                    force = query_input(
                        f'{len(missing)} files are missing, really convert now?',
                        lambda x: x in 'yn',
                        '[YN] ', 'N') == 'y'
                    if not force:
                        print('Please first download')
                        time.sleep(1)
                        continue

                print('Input MP4 filename\nPress Enter to bring up file browser')
                filename = input('? ')
                if not filename:
                    Tk().withdraw()
                    filename = tkinter.filedialog.asksaveasfilename(
                        initialfile=dirname + '-out.mp4',
                        filetypes=[('mp4', '*.mp4'), ('All files', '*.*')], defaultextension='.mp4')
                filename = os.path.abspath(filename)
                if not filename:
                    print('Canceled!')
                    time.sleep(1)
                    continue
                print(f'Saving to "{filename}"')

                start_offset_suggested = project['start_offset'] // 1000
                start_offset = query_input(
                    'Input stream start offset\n'
                    f'(suggested: {start_offset_suggested}, but multiple of 12 is best)\n'
                    'If unsure or A/V lost sync, use 0',
                    lambda x: x.isdigit() or x == 'q',
                    default_input='0')
                if start_offset == 'q':
                    print('Canceled!')
                    time.sleep(1)
                    continue

                args = [find_ffmpeg(), '-y']
                if int(start_offset) > 0:
                    args += ['-ss', f'{start_offset}']
                args += ['-i', project['playlist_patched'],
                         '-c', 'copy',
                         '-bsf:v', 'filter_units=remove_types=12',  # TODO: only for h264 streams!
                         '-movflags', '+faststart',
                         filename]
                p = subprocess.run(args, cwd=dirname)
                if p.returncode != 0:
                    print('Something went wrong!')
                else:
                    print('Done!')
            elif r == 'q':
                return
            time.sleep(1)
        except Exception as e:
            traceback.print_exc()
        except KeyboardInterrupt as e:
            print('Interrupted!')


def main():
    global g_manager
    try:
        while True:
            login_ok, _ = g_manager.check(access_web=False)
            login_info = 'Logged in' if login_ok else 'Not logged in'
            r = query_input(
                f'========== Main Menu ==========\n'
                f'Current login: {login_info}\n'
                f'1. Login / logout / status\n'
                f'2. Download JSON\n'
                f'3. Create project from JSON\n'
                f'4. Open project (for processing)\n'
                f'5. (Advanced) Create project from M3U8\n'
                f'Q. Quit',
                lambda x: x in '12345q',
                '[12345Q]? '
            )
            if r == 'q':
                return
            f = {
                '1': login_manage,
                '2': download_json,
                '3': create_project,
                '4': process_project,
                '5': create_project_from_m3u8,
            }[r]
            try:
                f()
            except (Exception, KeyboardInterrupt) as e:
                traceback.print_exc()
            login_manager.save_manager(g_manager)
    finally:
        login_manager.save_manager(g_manager)


if __name__ == '__main__':
    main()
