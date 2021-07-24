import hashlib


# https://stackoverflow.com/questions/12186993/what-is-the-algorithm-to-compute-the-amazon-s3-etag-for-a-file-larger-than-5gb

def s3_etag(file_or_bytes, multipart_chunksize=10 * 1024 * 1024):
    if isinstance(file_or_bytes, bytes):
        contents = iter(
            file_or_bytes[i:i + multipart_chunksize] for i in range(0, len(file_or_bytes), multipart_chunksize))
    else:
        file_or_bytes.seek(0)
        contents = iter(lambda: file_or_bytes.read(multipart_chunksize), b'')
    hashes = [hashlib.md5(part).digest() for part in contents]

    if len(hashes) > 1:
        md5hash = hashlib.md5()
        for h in hashes:
            md5hash.update(h)
        return f'"{md5hash.hexdigest()}-{len(hashes)}"'
    else:
        return f'"{hashes[0].hex()}"'


def guess_chunksize(file_or_bytes, filesize, etag, chunksize_step=1024):
    import re
    import math
    m = re.match(r'"[0-9a-f]{32}(?:-([0-9]+))?"', etag)
    if m is None:
        raise ValueError(f'Bad S3 etag {etag}')
    if m.group(1) is None:
        raise ValueError(f'Not chunked etag {etag}')
    chunk_count = int(m.group(1))
    max_size = math.ceil(filesize / (chunk_count - 1) + chunksize_step)
    min_size = math.floor(filesize / chunk_count)
    min_size = (min_size // chunksize_step) * chunksize_step  # align to step

    print(f'min {hex(min_size)} max {hex(max_size)}')

    # for chunksize in range(min_size, max_size, chunksize_step):
    #     print(f'try {chunksize}...')
    #     if etag == s3_etag(file_or_bytes, chunksize):
    #         return chunksize
    # else:
    #     raise ValueError('Cannot guess chunksize, maybe file is broken')

    min_size -= chunksize_step  # ensure valid cs is GREATER THAN min_size
    this_step = chunksize_step
    while this_step < max_size:
        this_step <<= 1
    while this_step >= chunksize_step:
        # half step for finer check (moved to top)
        this_step >>= 1
        # try 1*step 3*step 5*step ... etc
        start = (math.ceil((min_size - this_step) / (2 * this_step)) * 2 + 1) * this_step
        print(f'start {hex(start)} step size {hex(this_step)}')
        for chunksize in range(start, max_size, 2 * this_step):
            print(f'try {hex(chunksize)}...')
            if etag == s3_etag(file_or_bytes, chunksize):
                return chunksize
    else:
        raise ValueError('Cannot guess chunksize, maybe file is broken, or etag is encrypted')


def check_etag(file_or_bytes, etag, multipart_chunksize=10 * 1024 * 1024):
    return s3_etag(file_or_bytes, multipart_chunksize) == etag


def check_etag_header(file_or_bytes, header, multipart_chunksize=10 * 1024 * 1024):
    etag = header.get('etag', None)
    if etag is None:
        return True
    return check_etag(file_or_bytes, etag, multipart_chunksize)


def test_etag():
    FILENAME = 'test.ts'
    # with open(FILENAME, 'rb') as f:
    #     file_bytes = f.read()
    #     _etag = s3_etag(f, 10 * 1024 * 1024)
    # _etag = '"78cf3c0320a569dfc047ee3d03a499eb-2"'
    # cs = guess_chunksize(file_bytes, len(file_bytes), _etag, 1)
    # print(cs)
    with open(FILENAME, 'rb') as f:
        _etag = s3_etag(f, 10 * 1024 * 1024)
        print(_etag)
    if _etag == '"78cf3c0320a569dfc047ee3d03a499eb-2"':
        print('check ok')


if __name__ == '__main__':
    test_etag()
