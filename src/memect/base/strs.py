
import re
from collections.abc import Callable, Sequence
from typing import Final, Literal, Self


def to_bq(s:str)->tuple[str,str]:
    """
    s:任意字符
    半角字符和全角字符，返回(半角字符，全角字符)，如果没有，就返回原始的字符
    """
    if len(s)!=1:
        raise ValueError(f'只能够为1个字符:{s}')
    c = ord(s)
    if 0x21<= c <= 0x7E:
        # 半角
        return (chr(c), chr(c+65248))
    elif 0xFF01 <= c <= 0xFF5E:
        # 全角
        return (chr(c-65248), chr(c))
    elif c == 32 or c == 12288:
        # 空格
        return (chr(32), chr(12288))
    else:
        return (s,s)

_roman_map={
    '\u2160':'I',
    '\u2161':'II',
    '\u2162':'III',
    '\u2163':'IV',
    '\u2164':'V',
    '\u2165':'VI',
    '\u2166':'VII',
    '\u2167':'VIII',
    '\u2168':'IX',
    '\u2169':'X',
    '\u216a':'XI',
    '\u216b':'XII',
    '\u216c':'L',
    '\u216d':'C',
    '\u216e':'D',
    '\u216f':'M',
    #=========
    '\u2170':'i',
    '\u2171':'ii',
    '\u2172':'iii',
    '\u2173':'iv',
    '\u2174':'v',
    '\u2175':'vi',
    '\u2176':'vii',
    '\u2177':'viii',
    '\u2178':'ix',
    '\u2179':'x',
    '\u217a':'xi',
    '\u217b':'xii',
    '\u217c':'l',
    '\u217d':'c',
    '\u217e':'d',
    '\u217f':'m',
}

def roman(s:str)->str:
    """转换特殊的单个罗马字符为多个，如果不是，返回原始字符"""
    return _roman_map.get(s,s)

class NText:
    """字符串需要进行简单的归一化和在原字符串溯源，可以使用这个对象"""
    _alike_chars:dict[str,str]={
        #re.sub(r'[\u002d\u2014\uff0d]', '-', s),
        '\u002d':'-',
        '\u2014':'-',
        '\uff0d':'-',
    }
    """看起来一样的字符，归一化"""
    def __init__(self,raw_text:str,text:str,positions:Sequence[tuple[int,int]],*,parent:Self|None=None):
        super().__init__()
        assert len(text)==len(positions)
        self.raw_text:Final = raw_text
        """原始的字符串"""
        self.text:Final = text
        """归一化后的字符串"""
        self.positions:Final = positions
        """位置映射"""
        self.parent:Final[Self|None]=parent
    
    def get_range(self,start:int,end:int|None)->tuple[int,int]:
        i,j = self._get_range(start,end)
        if self.parent:
            return self.parent.get_range(i,j)
        else:
            return i,j

    def _get_range(self,start:int,end:int|None=None)->tuple[int,int]:
        """根据归一化后的文本的start，end，获得在原始文本的start，end"""
        n:Final = len(self.positions)
        if end is None:
            end = n
        if start<0:
            old_start = start
            start+=n
            if start<0:
                raise ValueError(f'start={old_start},len={n} out of range')
        if end<0:
            old_end = end
            end+=n
            if end<0:
                raise ValueError(f'end={old_end},len={n} out of range')

        i:int = self.positions[start][0]
        #因为归一化可以把1个字符映射为多个，不支持多个映射为1个，所以可以如下
        #这样更加好，如：
        #a   c => ac
        #然后当get_range(0,1)=>'a' 而不是'a   '
        j:int = self.positions[end-1][1] if end>0 else i
        if j<i:
            #当start=end，且删除了空格
            j=i
        #为了更好的处理空格(不可见)
        return (i,j)
    
    def get_text(self,start:int,end:int|None=None,*,strict:bool=True)->str:
        """获得原始的文本
        start: 在归一化后的文本的start
        end: 在归一化后的文本的end（不包括）
        """
        i,j = self._get_range(start,end)
        if self.parent:
            return self.parent.get_text(i,j)
        else:
            return self.raw_text[i:j]
    

    def strip(self)->Self:
        """去掉前后的空格"""
        return self.get(self.text,space='strip',parent=self)
    
    def b2q(self)->Self:
        return self.get(self.text,mode='b2q',parent=self)
    
    def q2b(self)->Self:
        return self.get(self.text,mode='q2b',parent=self)
    
    def roman(self)->Self:
        return self.get(self.text,roman=True,parent=self)
    
    def sub(self,pattern:str|re.Pattern[str],repl:str|Callable[[re.Match[str]],str])->'NText':
        """可以n个替换为n个，或者1个替换为n个，或者n个替换为1个，不允许2个替换为3个，或者3个替换为2个这样"""
        return _Rule(pattern,repl).run(self.text,parent=self)
    
    def normalize(self,*,mode:Literal['b2q','q2b']|None=None,space:Literal['remove','strip','keep_one']|None=None,roman:bool=False,rules:Sequence[tuple[str|re.Pattern[str],str|Callable[[re.Match[str]],str]]]|None=None)->Self:
        return self.get(self.text,mode=mode,space=space,rules=rules,roman=roman,parent=self)

    @classmethod
    def get(cls,s:str,/,*,mode:Literal['b2q','q2b']|None=None,space:Literal['remove','strip','keep_one']|None=None,alike:bool=False,roman:bool=False,rules:Sequence[tuple[str|re.Pattern[str],str|Callable[[re.Match[str]],str]]]|None=None,parent:Self|None=None)->Self:
        """对字符串进行归一化，返回新的字符串和对应的位置"""

        
        def normalize_char(c:str)->str:
            if roman:
                #"\u2160-\u216f","\u2170-\u217f"
                c1 = _roman_map.get(c)
                if c1 is not None:
                    #如果存在就直接返回？不需要再通过其他规则？因为会返回多个字符
                    return c1
            if alike:
                c1 = cls._alike_chars.get(c)
                if c1 is not None:
                    return c1
            if mode:
                c1,c2 = to_bq(c)
                if mode=='b2q':
                    #半角转换为全角
                    c = c2
                elif mode=='q2b':
                    #全角转换为半角
                    c = c1
                else:
                    #不需要改变
                    pass
            
            
            return c
        
        buf:list[str]=[]
        positions:list[tuple[int,int]]=[]

        start=0
        end=len(s)

        #前面的空格，包括换行
        m1 = re.search(r'^[\s]+',s)
        #后面的空格
        m2 = re.search(r'[\s]+$',s)
        if m1:
            start = m1.end()
        if m2:
            end = m2.start()

        for i,c in enumerate(s):
            #删除字符
            #一一对应
            #一个字符对应多个
            #添加字符
            if space=='strip' and (i<start or i>=end):
                c=''
            elif space is not None and c.isspace():
                if space=='remove':
                    c=''
                elif space=='keep_one':
                    #多个空格保留1个，如：abc   x => abc x
                    #前后的空格去掉
                    if i<start or i>=end:
                        #前后的空格去掉
                        c=''
                    elif i+1<end and s[i+1].isspace():
                        #中间的连续的空格保留最后一个
                        c=''
                    else:
                        #保留
                        pass
                else:
                    #不应该执行到这里
                    pass
            else:
                #1个变成n个
                c=normalize_char(c)
            if c:
                buf.append(c)
                #如果c变成多个字符，如：特殊的字符看起来是多个字的
                positions.extend([(i,i+1)]*len(c))
                
        #先创建一个对象
        s2=''.join(buf)
        if s==s2 and parent:
            #如果相同的，可以跳过去，减少计算，不跳过也可以
            nt = parent
        else:
            nt = cls(s,s2,positions,parent=parent)
        if rules:
            for p,r in rules:
                rule = _Rule(p,r)
                nt = rule.run(nt.text,parent=nt)

        return nt # type: ignore
        
class _Rule:
    def __init__(self,pattern:str|re.Pattern[str],repl:str|Callable[[re.Match[str]],str]):
        super().__init__()
        self._pattern:Final = re.compile(pattern) if isinstance(pattern,str) else pattern
        self._repl:Final = repl
    
    def run(self,text:str,parent:NText|None=None)->NText:
        positions:list[tuple[int,int]]=[]
        
        pos:Final={
            'start':0
        }
        def do_match(m:re.Match[str])->str:
            if isinstance(self._repl,str):
                return self._repl
            else:
                return self._repl(m)
            
        def do_repl(m:re.Match[str])->str:
            s1 = m.group()
            s2 = do_match(m)
            n1=m.end()-m.start()
            n2 = len(s2)
            i = m.start()

            assert n1>0

            if False:
                if s1.isspace() and s2.isspace():
                    #如果s1全部是空格等，认为1个即可
                    n1=1

            if i>pos['start']:
                for k in range(pos['start'],i):
                    positions.append((k,k+1))
            
            pos['start']=m.end()

            if n1==n2:
                #如果是一一对应，如：abc => xyz
                for k in range(n1):
                    positions.append((i+k,i+k+1))
            elif n1<n2:
                #Ⅷ => viii
                #严格要求
                if n1!=1:
                    raise ValueError('')
                
                for k in range(n2):
                    positions.append((i,i+1))
            elif n2==0:
                #删除空格等
                pass    
            else:
                #viii => Ⅷ
                if n2!=1:
                    raise ValueError('')
                
                positions.append((i,i+n1))
                
            return s2

        new_text = self._pattern.sub(do_repl,text)
        for k in range(pos['start'],len(text)):
            positions.append((k,k+1))
        return NText(text,new_text,positions,parent=parent)




