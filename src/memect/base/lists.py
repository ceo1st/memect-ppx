import functools
from collections.abc import Callable, Iterable, MutableSequence, Sequence, Iterator
from typing import Any, Final

# ===================================
"""提供支持输入多个参数的简便函数"""

_builtin_sorted: Final = sorted


def insert[T](seq: MutableSequence[T], index: int, *others: Iterable[T] | None):
    """在指定的位置插入多个元素，对应list.insert"""
    for other in others:
        if other is not None:
            for obj in other:
                seq.insert(index, obj)
                index += 1


def extend[T](seq: MutableSequence[T], *others: Iterable[T] | None):
    """添加其他集合的元素到集合，对应list.extend()"""
    for other in others:
        if other is not None:
            seq.extend(other)


def remove[T](
    seq: MutableSequence[Any],
    *others: Iterable[T] | None,
    use_is: bool = False,
    repeating: bool = False,
    strict: bool = True,
) -> list[T]:
    """
    从objs中删除部分对象，对应list.remove()
    """
    deleted_objs: list[T] = []

    def is_equals(a: T, b: T) -> bool:
        if use_is:
            return a is b
        else:
            return a == b

    for other in others:
        if other is None:
            continue
        for obj in other:
            found = 0
            i = 0
            while i < len(seq):
                if is_equals(obj, seq[i]):
                    found += 1
                    deleted_objs.append(seq[i])
                    del seq[i]
                    # 需要继续下去还是跳出，如果使用is的，认为是需要继续下去了
                else:
                    i += 1

                if found > 0 and not repeating:
                    break
            if found == 0 and strict:
                raise ValueError(f"{obj} not in list")
    return deleted_objs


def remove2[**P, T](
    seq: MutableSequence[T], fn: Callable[[int, Sequence[T]], bool]
) -> list[T]:
    """
    删除对象
    objs:[],
    fn:(i,objs){}，返回True表示删除，False表示保留
    """

    i: int = 0
    deleted_objects: list[T] = []
    n = len(seq)
    while i < n:
        # fn(i,seq)比fn(i,seq,*args,**kwargs)快一倍
        # 所以这里不直接fn(i,seq,*args,**kwargs)
        if fn(i, seq):
            deleted_objects.append(seq[i])
            del seq[i]
            n -= 1
        else:
            i += 1
    return deleted_objects


def _get_indexes[T](seq: MutableSequence[T], indexes: Iterable[int]) -> list[int]:
    """排序index，且清除重复的"""
    total = len(seq)
    new_indexes: list[int] = []
    for i in set(indexes):
        if i >= total or (i < 0 and i + total < 0):
            raise ValueError(f"index超过范围:total={total},index={i}")
        if i < 0:
            i = total + i
        new_indexes.append(i)
    new_indexes.sort()
    return new_indexes


def pop[T](seq: MutableSequence[T], indexes: Iterable[int]) -> list[T]:
    """删除多个元素且返回，对应list.pop()"""
    # 因为需要遍历2次，先转换为tuple
    indexes = tuple(indexes)
    objs: list[T] = []
    for i in indexes:
        objs.append(seq[i])

    n = 0
    for i in _get_indexes(seq, indexes):
        seq.pop(i - n)
        n += 1
    return objs


def delete[T](seq: MutableSequence[T], indexes: Iterable[int]):
    """删除多个元素且，对应del list[i]"""
    n = 0
    for i in _get_indexes(seq, indexes):
        del seq[i - n]
        n += 1


def replace[T](
    seq: MutableSequence[T],
    old_objs: Iterable[T],
    new_objs: Iterable[T],
    strict: bool = False,
):
    """替代元素
    seq:
    old_objs:
    new_objs:
    strict: True表示严格的，也就是old_objs和new_objs必须一一对应替换，False表示删除旧的，然后插入新的
    """
    if strict:
        for obj1, obj2 in zip(old_objs, new_objs):
            i = seq.index(obj1)
            seq[i] = obj2
    else:
        i = -1
        for obj in old_objs:
            i = seq.index(obj)
            del seq[i]
        if i == -1:
            raise ValueError(f"old_objs为空")
        # 插入到最后一个元素的地方
        insert(seq, i, new_objs)


def filter[T](objs: Iterable[T], fn: Callable[[T], bool]) -> Iterator[T]:
    for obj in objs:
        if fn(obj):
            yield obj


def filter2[T](
    objs: Sequence[T], fn: Callable[[int, Sequence[T]], bool]
) -> Iterator[T]:
    for i in range(len(objs)):
        if fn(i, objs):
            yield objs[i]


def flat[T](seq: Iterable[Iterable[T]]) -> list[T]:
    """
    把[[a,b],[c,d]] => [a,b,c,d]
    """
    items: list[T] = []
    for a in seq:
        items.extend(a)
    return items


def sorted[T](
    seq: Iterable[T], cmp: Callable[[T, T], int], reverse: bool = False
) -> list[T]:
    """
    提供python2的sorted函数
    """
    K = functools.cmp_to_key(cmp)
    return _builtin_sorted(seq, key=lambda obj: K(obj), reverse=reverse)


def sort[T](seq: list[T], cmp: Callable[[T, T], int], reverse: bool = False):
    """
    直接对seq进行sort，等同于seq.sort()
    """
    K = functools.cmp_to_key(cmp)
    seq.sort(key=lambda obj: K(obj), reverse=reverse)


def distinct[T](
    objs: MutableSequence[T], cmp: Callable[[T, T], bool] | None = None
) -> list[T]:
    """
    对于相同的对象，仅仅保留一个，因为如果直接使用set()，需要对象支持hash
    objs:[],
    cmp:fn(a,b)

    返回被删除的对象
    """
    if cmp is None:

        def cmp2(a: T, b: T) -> bool:
            return a == b

        cmp = cmp2

    deleted_objs: list[T] = []
    i = 0
    while i < len(objs):
        obj1 = objs[i]
        j = i + 1
        while j < len(objs):
            obj2 = objs[j]
            if cmp(obj1, obj2):
                deleted_objs.append(objs[j])
                del objs[j]

            else:
                j += 1
        i += 1
    return deleted_objs


def split[T](
    objs: Sequence[T],
    *,
    sizes: Iterable[int] | None = None,
    size: int | None = None,
    group_size: int | None = None,
) -> Iterator[Sequence[T]]:
    """分成多个返回

    objs:
    sizes:[1,2,3] 表示分成多少个组，每个组有多少个
    size: 2  表示每组2个
    group_size: 表示分成n个组
    """
    # sizes
    # _type: type[T] = typing.cast(type, typing.get_origin(list_type))
    if sizes is not None:
        i = 0
        for size in sizes:
            group: Any = objs[i : i + size]
            if len(group) != size:
                raise ValueError(f"需要{size}个，现在只有{len(group)}个")
            yield group
            i += size
    elif size is not None and size > 0:
        for i in range(0, len(objs), size):
            yield objs[i : i + size]
    elif group_size is not None and group_size > 0:
        total = len(objs)
        n = total // group_size
        r = total % group_size
        start = 0
        for i in range(group_size):
            end = start + n
            if i < r:
                end += 1
            yield objs[start:end]
            start = end
            # 如果没有了，就退出了，不返回空数组
            if start >= total:
                break
    else:
        raise ValueError("sizes,chunk_size和worker_size必须且只能够设置1个")


def iters[T](*objs: Iterable[T] | None) -> Iterator[T]:
    """连续迭代多个，对应iter"""
    for obj in objs:
        if obj is not None:
            yield from obj


def join[T](seqs:Iterable[Iterable[T]])->list[T]:
    """合并多个数组为一个"""
    a:list[T]=[]
    for seq in seqs:
        a.extend(seq)
    return a