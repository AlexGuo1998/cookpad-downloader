import time
import typing

T = typing.TypeVar('T')


@typing.overload
def query_input(prompt, validator: typing.Callable[[str], bool],
                prompt1='? ', default_input='', lower_case=True) -> str:
    ...


def query_input(prompt, validator: typing.Callable[[str], T],
                prompt1='? ', default_input='', lower_case=True) -> T:
    while True:
        print(prompt)
        if default_input:
            print(f'Press Enter for default ({default_input})')
        r = input(prompt1)
        if not r:
            r = default_input
        r_match = r.lower() if lower_case else r
        validate_result = validator(r_match)
        if r_match and validate_result:
            if validate_result is True:
                return r_match
            else:
                return validate_result
        print(f'Invalid input: {r}')
        time.sleep(1)
