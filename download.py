import os
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
import io
import typing
import json

import requests
import requests.structures
try:
    import tqdm
except ModuleNotFoundError:
    tqdm = None

from s3_etag import check_etag_header

validator = check_etag_header


@dataclass
class DownloaderOptions:
    chunk_size: int = 10 * 1024  # bytes for 1 iteration
    timeout_connect: int = 10
    timeout_read: int = 10
    min_rate: int = 5 * 1024  # byte / sec
    headers: typing.Dict[str, str] = field(default_factory=dict)
    cookies: typing.Dict[str, str] = field(default_factory=dict)
    proxies: typing.Dict[str, str] = field(default_factory=dict)

    temp_suffix: str = '.download'
    retry: int = 3  # on connection fail (i.e. 0 bytes during resuming)
    # validator: typing.Callable[[typing.BinaryIO, requests.structures.CaseInsensitiveDict, ...], bool] = None
    # validator_args: typing.Sequence[typing.Any] = field(default_factory=list)
    use_validator: bool = True
    validator_chunk_size: int = 10 * 1024 * 1024
    validate_retry: int = 1
    retry_delay: int = 1  # delay between retries

    queue_size: int = 3
    progress_bar_ascii: typing.Any = True if os.name == 'nt' else None

    hide_progress_bar: bool = False
    no_output: bool = False


class DownloadStatus(Enum):
    IDLE = 0
    RUNNING = 1
    DONE = 2
    FAILED = 3


class SingleDownloader:
    def __init__(self, url: str, out_file: typing.Union[str, typing.BinaryIO], options: DownloaderOptions = None,
                 callback=None, session=None):
        self.url = url
        self.out_file = out_file
        self.options = options or DownloaderOptions()
        self._status: DownloadStatus = DownloadStatus.IDLE
        self._thread: typing.Optional[threading.Thread] = None
        self._can_resume = False
        self.request_stop = False
        self.status_string = 'Idle'
        self.size_dl = 0
        self.size_all = -1
        self.callback = callback
        self.session = session or requests.Session()

    def start_threaded(self):
        if self._status not in (DownloadStatus.IDLE, DownloadStatus.FAILED):
            return
        self._thread = threading.Thread(target=self.start)
        self.status_string = 'Staring'
        self._status = DownloadStatus.RUNNING
        self.callback and self.callback(self)
        self._thread.start()

    def status(self) -> DownloadStatus:
        if self._status == DownloadStatus.RUNNING:
            if not self._thread.is_alive():
                self._status = DownloadStatus.FAILED
        return self._status

    def start(self):
        self.status_string = 'Dl'
        self._status = DownloadStatus.RUNNING
        self.callback and self.callback(self)
        temp_fn = None
        use_temp = isinstance(self.out_file, str)
        # use temp file for downloading to filename, direct for file object (memory io etc)
        try:
            if use_temp:
                temp_fn = self.out_file + self.options.temp_suffix
                with open(temp_fn, 'w+b') as f:
                    ok = self._download_temp_file(f)
            else:
                ok = self._download_temp_file(self.out_file)
            if ok:
                if use_temp:
                    self.status_string = 'Moving file'
                    self.callback and self.callback(self)
                    os.replace(temp_fn, self.out_file)
                self.status_string = 'Done'
                self._status = DownloadStatus.DONE
            else:
                self._status = DownloadStatus.FAILED
            self.callback and self.callback(self)
        finally:
            if temp_fn is not None:
                try:
                    os.remove(temp_fn)
                except FileNotFoundError:
                    pass

    def _download_temp_file(self, f):
        validate_count = 0
        retry_count = 0
        while True:
            self.update_status_string(retry_count, validate_count)

            done, error, headers = self._download_piece(f)
            if not done:
                if error <= 0:
                    # simply retry
                    retry_count = 0
                else:
                    if error > 1:
                        # hard error, truncate file
                        f.truncate(0)
                    # retry with limit
                    retry_count += 1
                    if retry_count > self.options.retry:
                        error_type = 'soft' if error <= 1 else 'hard'
                        self.status_string = f'Retry count exceed ({error_type} error)'
                        return False
                    self.update_status_string(retry_count, validate_count)
                    time.sleep(self.options.retry_delay)
            else:
                retry_count = 0
                # validate
                if self.options.use_validator:
                    valid = validator(f, headers, self.options.validator_chunk_size)
                else:
                    valid = True
                if valid:
                    return True
                validate_count += 1
                f.truncate(0)
                if validate_count > self.options.validate_retry:
                    self.status_string = f'Validation failed (contact author if this happens all times)'
                    return False

    def update_status_string(self, retry_count, validate_count):
        status_string = 'Dl'
        if retry_count > 0:
            status_string += f' (retry #{retry_count})'
        if validate_count > 0:
            status_string += f' (val. fail #{validate_count})'
        self.status_string = status_string
        self.callback and self.callback(self)

    def _download_piece(self, f):
        # return: (done, error, headers)
        # error:
        # 0=slight(conn. broken)
        # 1=soft(can not connect)
        # 2=hard(bad status_code etc.)
        done = False
        downloaded_bytes = 0
        error = 0
        response_headers = {}
        try:
            headers = {'User-Agent': None}
            headers.update(self.options.headers)
            if self._can_resume:
                f.seek(0, io.SEEK_END)
                start_len = f.tell()
                headers['Range'] = f'bytes={start_len}-'
            else:
                f.seek(0, io.SEEK_SET)
                start_len = 0
            r = self.session.get(
                self.url,
                headers=headers,
                cookies=self.options.cookies,
                timeout=(self.options.timeout_connect, self.options.timeout_read),
                proxies=self.options.proxies,
                stream=True,
            )
            response_headers = r.headers
            try:
                r.raise_for_status()
            except requests.exceptions.HTTPError:
                error = 2
                raise
            # print('headers:')
            # print(response_headers)
            self._can_resume = response_headers.get('Accept-Ranges') == 'bytes'
            total_bytes = int(response_headers.get('Content-Length', -1))

            self.size_dl = start_len
            self.size_all = start_len + total_bytes if total_bytes >= 0 else -1
            self.callback and self.callback(self)

            t = time.time()
            download_finished = False
            for data in r.iter_content(self.options.chunk_size):
                # print(len(data))
                downloaded_bytes += len(data)
                self.size_dl = start_len + downloaded_bytes
                self.callback and self.callback(self)
                f.write(data)
                # test slow
                t1 = time.time()
                rate = len(data) / (t1 - t + 0.0001)
                t = t1
                if rate < self.options.min_rate:
                    # print('rate too slow')
                    break
            else:
                download_finished = True
            done = (total_bytes < 0 and download_finished) or downloaded_bytes == total_bytes
        except requests.exceptions.RequestException:
            if downloaded_bytes <= 0:
                # only set error for empty payloads
                error = 1 if error <= 1 else error
        return done, error, response_headers


class DownloadQueue:
    def __init__(self, tasks, options: DownloaderOptions = None):
        self.tasks: typing.List[typing.Tuple[str, str, str]] = tasks
        self.results: typing.List[typing.Tuple[bool, str]] = []
        self.options = options
        self.running = False
        self.task_queue = queue.SimpleQueue()  # id url filename info
        self.result_queue = queue.SimpleQueue()  # is_message? id success message

        if self.options.no_output or tqdm is None:
            self.options.hide_progress_bar = True

    def run(self):
        self.task_queue.empty()
        self.result_queue.empty()
        threads = []
        results = []
        try:
            self.running = True
            if self.options.hide_progress_bar:
                target = self.download_thread_no_bar
            else:
                target = self.download_thread
            for i in range(self.options.queue_size):
                threads.append(threading.Thread(
                    target=target, name=f'Download #{i}',
                    args=(i,)))
            for thread in threads:
                thread.start()
            for i, (url, filename, info) in enumerate(self.tasks):
                self.task_queue.put((i, url, filename, info))
            if self.options.hide_progress_bar:
                results = self._poll_download_tasks()
            else:
                with tqdm.tqdm(
                        total=len(self.tasks), position=0, ascii=self.options.progress_bar_ascii,
                        unit='file', miniters=1
                ) as bar:
                    results = self._poll_download_tasks(bar=bar)
        finally:
            self.running = False
            # TODO: stop immediately
            for thread in threads:
                thread.join()
            self.results = results

    def _poll_download_tasks(self, bar=None):
        finish_count = 0
        results = [(False, '') for i in range(len(self.tasks))]
        try:
            while finish_count < len(self.tasks):
                is_message, i, success, info = self.result_queue.get()
                if is_message:
                    if bar is None:
                        print(info)
                else:
                    results[i] = (success, info)
                    if bar is not None:
                        bar.update(1)
                    finish_count += 1
        except KeyboardInterrupt:
            msg = 'Please wait for running downloads to finish...'
            if bar is not None:
                bar.write(msg)
            elif not self.options.no_output:
                print(msg)
            raise
        return results

    def download_thread(self, index):
        with tqdm.tqdm(
                leave=False, position=index + 1, ascii=self.options.progress_bar_ascii,
                unit='B', unit_scale=True, unit_divisor=1024, miniters=1,
        ) as bar:
            def callback(dl: SingleDownloader):
                nonlocal downloaded, bar, status
                delta = dl.size_dl - downloaded
                downloaded = dl.size_dl
                bar.total = dl.size_all
                if status != dl.status_string:
                    status = dl.status_string
                    bar.set_postfix_str(status)
                bar.update(delta)

            session = requests.Session()
            while self.running:
                try:
                    i, url, filename, desc = self.task_queue.get(timeout=1)
                except queue.Empty:
                    continue
                dl = SingleDownloader(url, filename, self.options, callback, session=session)
                downloaded = 0
                status = ''
                bar.reset()
                bar.set_description(desc)
                try:
                    dl.start()
                    success = dl.status() == DownloadStatus.DONE
                    info = dl.status_string
                except Exception as e:
                    success = False
                    info = traceback.format_exc()
                self.result_queue.put((False, i, success, info))

    def download_thread_no_bar(self, index):
        def callback(dl: SingleDownloader):
            nonlocal status, desc, i
            if status != dl.status_string:
                status = dl.status_string
                if not self.options.no_output and status != 'Moving file':
                    self.result_queue.put((True, i, None, f'{desc}: {status}'))
                    # print(f'{desc}: {status}')

        session = requests.Session()
        while self.running:
            try:
                i, url, filename, desc = self.task_queue.get(timeout=1)
            except queue.Empty:
                continue
            dl = SingleDownloader(url, filename, self.options, callback, session=session)
            status = ''
            try:
                dl.start()
                success = dl.status() == DownloadStatus.DONE
                info = dl.status_string
            except Exception as e:
                success = False
                info = traceback.format_exc()
            self.result_queue.put((False, i, success, info))


def get_downloader_options(show_exceptions=False):
    do = DownloaderOptions()
    try:
        with open('downloader_option.json', 'r', encoding='utf-8') as f:
            j = json.load(f)
        do.__dict__.update({k: v for k, v in j.items() if k in do.__dict__})
    except Exception:
        if show_exceptions:
            traceback.print_exc()
    return do


def save_downloader_options(do):
    with open('downloader_option.json', 'w', encoding='utf-8') as f:
        json.dump(do.__dict__, f, indent=2)


def _test_single():
    opt = DownloaderOptions()
    url = 'https://d36k9ub4u1q07d.cloudfront.net/episode_archives/886/video/e55b0b90-69f2-46ab-8a89-bbfc3dd96ea0_hls_high_00001.ts'
    dl = SingleDownloader(url, 'test/test.bin', opt)
    dl.start_threaded()
    while dl.status() in (DownloadStatus.IDLE, DownloadStatus.RUNNING):
        a, b = dl.size_dl, dl.size_all
        if b <= 0:
            progress = f'    ? % {a}/-1'
        else:
            progress = f'{a / b:7.2%} {a}/{b}'
        print(f'{progress} {dl.status_string}')
        time.sleep(0.5)
    print(dl.status_string)


def _test_queue():
    opt = DownloaderOptions()
    tasks = [
        (
            f'https://d36k9ub4u1q07d.cloudfront.net/episode_archives/886/video/e55b0b90-69f2-46ab-8a89-bbfc3dd96ea0_hls_high_{i:05d}.ts',
            f'test/e55b0b90-69f2-46ab-8a89-bbfc3dd96ea0_hls_high_{i:05d}.ts',
            f'file #{i}'
        ) for i in range(200, 300)
    ]
    dq = DownloadQueue(tasks, opt)
    dq.run()


if __name__ == '__main__':
    _test_single()
    _test_queue()
