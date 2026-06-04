from dataclasses import KW_ONLY, dataclass
import logging
from typing import Iterable, Literal, Sequence



from memect.base.debug import XDebugger

from memect.base.bbox import BBox
from memect.base import lists
from memect.pdf.base import KPage



type _Line=list[float]

type _ReadOnlyLine=Sequence[float]

@dataclass
class Options:
    _:KW_ONLY
    x_d:int=1
    """垂直线x0,x1误差，在这个误差内认为是垂直线"""
    y_d:int=1
    """水平线y0,y1的误差，在这个误差内认为是水平线"""
    
    x_axis_d:int=2
    """如果x轴上的点间距在这个范围内，合并为一个，同时调整对应的线的x0或者x1，也就是水平对齐"""
    y_axis_d:int=2
    """如果y轴上的点间距在这个范围内，合并为一个，同时调整对应的线的y0或者y1，也就是垂直对齐"""

    min_h_length:int=3
    """水平线的最小长度"""
    min_v_length:int=3
    """垂直线的最小长度"""

    join_x_d:int=3
    """水平线之间间隔在这个范围内连接为在一起"""

    join_y_d:int=3
    """垂直线间隔在这个范围内连接在一起"""

    cross_x_d:int=3
    """水平线和垂直线的误差，在这个误差内会自动连接，如：|(d)----"""
    cross_y_d:int=3
    """垂直线和水平线的误差，在这个误差内会自动连接"""

class Liner:
    """调整表格的线，更加准确"""
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    _degugger=XDebugger(f'{__module__}.{__qualname__}')

    def __init__(self):
        super().__init__()
        self._page:KPage|None=None
        """如果设置了，可以用来显示过程"""
    

    def parse(self,lines:Iterable[Sequence[float]],*,bbox:BBox|None=None,options:Options|None=None,page:KPage|None=None)->list[tuple[float,float,float,float]]:
        #TODO 现在来自pdf的线都处理的很干净的了，options的参数可以设置得更小一些，现在还是使用以前的设置
        #根据线构造出表格
        #允许有误差，然后调整为精确的格式
        options = options or Options()
        self._page = page
        #复制一个，目的是需要修改
        _lines =[list(line) for line in lines]
        if not _lines:
            return []
        
        if bbox is None:
            bbox = BBox.join(_lines)
            assert bbox is not None
        else:
            #如果提供了bbox，但是bbox稍微大了一点？如：
            #bbox[0]-----------------bbox[2]
            #                       | =>这条线存在一点误差，偏移了一点，如果偏移很多，可以认为还有一列，如果偏移很少，bbox可以调整小一点？
            pass

        #确保在bbox内，调整溢出的线
        self._ensure_lines(_lines,bbox)
        #确保水平线(y0==y1)垂直线(x0==x2)
        self._filter_lines(_lines,x_d=options.x_d,y_d=options.y_d)
        #如果存在间距很小的线，调整移动对齐
        self._align_lines(_lines,x_d=options.x_axis_d,y_d=options.y_axis_d)
        #把靠近的线连接为在一起，如：-- --  => -----
        self._join_lines(_lines,x_d=options.join_x_d,y_d=options.join_y_d)
        #确保水平线和垂直相交，不存在“悬”线
        self._cross_lines(_lines,bbox,min_width=options.min_h_length,min_height=options.min_v_length,x_d=options.cross_x_d,y_d=options.cross_y_d)
        
        #因为可能存在双层线，需要去掉，仅仅去掉四周的，也可以通过增加options.x_d,y_d，但是这样是对所有的线
        #-------line1------
        #-------line2------
        self._fix0(_lines)

        #彩色表头
        self._fix1(_lines)
        
        #清除局部的线
        self._fix2(_lines)
        #清除悬空的线
        self._fix3(_lines)

        return [tuple(line) for line in _lines] # type: ignore
        #return cast(list[_Line[T]],_lines)
        

    def _eq(self,a:float,b:float,*,d:int=0)->bool:
        #math.isclose(a,b)
        if d==0:
            return a==b
        else:
            return abs(a-b)<=d
    
    def _middle(self,a:float,b:float)->float:
        if isinstance(a,int) and isinstance(b,int):
            return (a+b)//2
        else:
            return round((a+b)/2,1)
    
    def _split_lines(self,lines:list[_Line])->tuple[list[_Line],list[_Line]]:
        h_lines:list[_Line]=[]
        v_lines:list[_Line]=[]
        
        for line in lines:
            if line[0]==line[2]:
                #v_lines.append(list(line))
                v_lines.append(line)
            elif line[1]==line[3]:
                #h_lines.append(list(line))
                h_lines.append(line)
            else:
                pass   
        return h_lines,v_lines
    
    def _filter_lines(self,lines:list[_Line],*,x_d:int=1,y_d:int=1):
        """确保为水平线和垂直线"""
        i=0
        while i<len(lines):
            line = lines[i]
            d1 = line[2]-line[0]
            d2 = line[3]-line[1]
            if d1<=d2 and d1<=x_d:
                #垂直线
                line[0]=line[2]
                i+=1
            elif d2<=d1 and d2<=y_d:
                #水平线
                line[1]=line[3]
                i+=1
            else:
                #删除斜线?
                del lines[i]

    def _align_lines(self,lines:list[_Line],*,x_d:int=2,y_d:int=2):
        """调整相邻的线使用相同的坐标"""

        def update_lines(lines:list[_Line],old_x:float,new_x:float,i:int,j:int):
            for line in lines:
                if line[i]==old_x:
                    line[i]=new_x
                if line[j]==old_x:
                    line[j]=new_x
        
        def fix_lines(axis:list[float],lines:list[_Line],index:Literal[0,1],d:int=2):
            axis = sorted(axis)
            i=1
            while i<len(axis):
                x0=axis[i-1]
                x1=axis[i]
                if i+1<len(axis):
                    x2 = axis[i+1]
                    d1 = x1-x0
                    d2 = x2-x1
                    if d1<=d2 and d1<=d:
                        update_lines(lines,x1,x0,index,index+2)
                        del axis[i]
                    elif d2<=d1 and d2<=d:
                        update_lines(lines,x1,x2,index,index+2)
                        del axis[i]
                    else:
                        i+=1
                elif x1-x0<=d:
                    update_lines(lines,x1,x0,index,index+2)
                    del axis[i]
                else:
                    i+=1
        
        x_axis:list[float]=[]
        y_axis:list[float]=[]
        for line in lines:
            if line[0]==line[2]:
                #垂直线
                x_axis.append(line[0])
                y_axis.append(line[1])
                y_axis.append(line[3])
            elif line[1]==line[3]:
                #水平线
                y_axis.append(line[1])
                x_axis.append(line[0])
                x_axis.append(line[2])
            else:
                #斜线?
                pass
        
        x_axis = list(set(x_axis))
        y_axis = list(set(y_axis))
        fix_lines(x_axis,lines,0,d=x_d)
        fix_lines(y_axis,lines,1,d=y_d)
    

    def _ensure_lines(self,lines:list[_Line],bbox:BBox):
        #线确保所有的线都在bbox内
        k=0
        while k<len(lines):
            line = lines[k]
            for i in [0,1]:
                if line[i]>=bbox[i+2] or line[i+2]<=bbox[i]:
                    del lines[k]
                    break
                if line[i]<bbox[i]:
                    #line[i]---|----
                    line[i]=bbox[i]
                
                if bbox[i+2]<line[i+2]:
                    #----|---line[i+2]
                    line[i+2]=bbox[i+2]
            else:
                k+=1
        #总是先添加4条边界线
        lines.append([bbox[0],bbox[3],bbox[2],bbox[3]])
        lines.append([bbox[0],bbox[1],bbox[2],bbox[1]])
        lines.append([bbox[0],bbox[1],bbox[0],bbox[3]])
        lines.append([bbox[2],bbox[1],bbox[2],bbox[3]])
    
    def _join_lines(self,lines:list[_Line],x_d:int=3,y_d:int=3):
        #把靠近的线合并为一条，如：-- -- => ----
        #因为在前面的步骤已经对齐了线，就不需要考虑误差了

        def split_group(lines:list[_Line],index:int)->list[_Line]:
            group:list[_Line]=[lines.pop(0)]
            while lines:
                line = lines[0]
                v = group[0][index]
                if line[index]==v:
                    group.append(line)
                    del lines[0]
                else:
                    #因为已经排序了，如果不一致的就不需要继续了
                    break
            return group
        
        def adjust_group(lines:list[_Line],is_h:bool,d:int=3):
            if is_h:
                ix0,iy0,ix1,iy1=0,1,2,3 #type:ignore
            else:
                ix0,iy0,ix1,iy1=1,0,3,2 #type:ignore
            lines.sort(key=lambda line:line[ix0])
            i=1
            while i<len(lines):
                line1 = lines[i-1]
                line2 = lines[i]
                if line2[ix0]-line1[ix1]<=d:
                    #---(line1)--[d]--(line2)---
                    line1[ix1]=max(line1[ix1],line2[ix1])
                    del lines[i]
                else:
                    i+=1
                pass
        
        def join(lines:list[_Line],is_h:bool,d:int=3):
            if is_h:
                #水平线
                ix0,iy0,ix1,iy1=0,1,2,3 #type:ignore
            else:
                #垂直线
                ix0,iy0,ix1,iy1=1,0,3,2 #type:ignore

            lines.sort(key=lambda line:line[iy0])
            new_lines:list[_Line]=[]
            while lines:
                group = split_group(lines,iy0)
                adjust_group(group,is_h,d=d)
                new_lines.extend(group)
                
            
            lines.clear()
            lines.extend(new_lines)
        
        h_lines,v_lines = self._split_lines(lines)
        

        join(h_lines,True,d=x_d)
        join(v_lines,False,d=y_d)

        lines.clear()
        lines.extend(h_lines)
        lines.extend(v_lines)

    def _cross_lines(self,lines:list[_Line],bbox:BBox,*,min_width:int=3,min_height:int=3,x_d:int=3,y_d:int=3):
        
        def remove_lines(lines:list[_Line],index:int,min_value:int=3):
            i=0
            while i<len(lines):
                line = lines[i]
                if line[index+2]-line[index]<min_value:
                    del lines[i]
                else:
                    i+=1
        

        def fix_point(h_line:_Line,cross_lines:list[_Line],ix:int,is_left:bool,d:int=3)->tuple[bool,bool]:
            ok=False
            changed=False
            start=-1
            end=-1


            cross_lines.sort(key=lambda line:line[ix],reverse=not is_left)

            for i in range(len(cross_lines)):
                v_line = cross_lines[i]
                if is_left:
                    if v_line[ix]==h_line[ix]:
                        #|-----
                        ok=True
                        break
                    elif v_line[ix]<h_line[ix]:
                        #| ----
                        start=i
                    else:
                        #| --|---
                        end=i
                        break
                else:
                    if v_line[ix]==h_line[ix]:
                        #----|
                        ok=True
                        break
                    elif v_line[ix]>h_line[ix]:
                        #--- |
                        start = i
                    else:
                        #--|-- |
                        end = i
                        break
            if not ok and start>=0:
                if end>start:
                    #| --|--- 
                    v_line1 = cross_lines[start]
                    v_line2 = cross_lines[end]
                    #| --|--- is_left=True
                    #--|-- |  is_left=True
                    if is_left:
                        d1 = h_line[ix]-v_line1[ix]
                        d2 = v_line2[ix]-h_line[ix]
                    else:
                        d1 = v_line1[ix]-h_line[ix]
                        d2 = h_line[ix]-v_line2[ix]
                    if d1<=d2 and d1<=d:
                        #|--|---- is_left=True
                        #--|--|
                        h_line[ix]=v_line1[ix]
                        ok=True
                        changed=True
                    elif d2<=d1 and d2<=d:
                        #|  |----  is_left=True
                        #---| |    is_left=False
                        h_line[ix]=v_line2[ix]
                        ok=True
                        changed=True
                    else:
                        #如果误差过大，就放弃了这条线？如：
                        #|    -----|----
                        pass
                else:
                    #| ----- is_left=True
                    #---- | is_left=False
                    if is_left:
                        d1 = h_line[ix]-v_lines[start][ix]
                    else:
                        d1 = v_lines[start][ix]-h_line[ix]
                    
                    if d1<=d:
                        #|------
                        #------|
                        h_line[ix]=v_lines[start][ix]
                        ok=True
                        changed=True
                    else:
                        pass
            return ok,changed

        def fix_line(h_line:_Line,v_lines:list[_Line],is_h:bool,*,d:int=3):
            if is_h:
                ix0,iy0,ix1,iy1=0,1,2,3
            else:
                ix0,iy0,ix1,iy1=1,0,3,2
            
            cross_lines:list[list[float]]=[]
            for v_line in v_lines:
                if  v_line[iy0]<=h_line[iy0]<=v_line[iy1]:
                    #|-------
                    cross_lines.append(v_line)
            
            
            left_ok,left_changed = fix_point(h_line,cross_lines,ix0,True,d=d)
            right_ok,right_changed = fix_point(h_line,cross_lines,ix1,False,d=d)

            if h_line[ix1]-h_line[ix0]==0:
                #极端的情况，如：
                #|  --|--  |  两端都缩到中间了
                return False,False
            else:
                return left_ok and right_ok,left_changed or right_changed
            

        def fix_once(h_lines:list[_Line],v_lines:list[_Line],*,x_d:int=3,y_d:int=3)->bool:
            #x_d: 水平线和垂直线的误差，在这个范围内水平线会连接到垂直线
            #y_d: 垂直线和水平线的误差，在这个范围内垂直线会连接到水平线
            changed=False
            i=0
            while i<len(h_lines):
                ok,_changed = fix_line(h_lines[i],v_lines,True,d=x_d)
                changed = changed or _changed
                if not ok:
                    del h_lines[i]
                else:
                    i+=1
            
            i=0
            while i<len(v_lines):
                ok,_changed = fix_line(v_lines[i],h_lines,False,d=y_d)
                changed = changed or _changed
                if not ok:
                    del v_lines[i]
                else:
                    i+=1
            return changed
            


        h_lines,v_lines = self._split_lines(lines)
        remove_lines(h_lines,0,min_value=min_width)
        remove_lines(v_lines,1,min_value=min_height)

        debug:bool=False
        if debug:
            h_lines.sort(key=lambda line:line[1])
            v_lines.sort(key=lambda line:line[0])
        while True:
            if not fix_once(h_lines,v_lines,x_d=x_d,y_d=y_d):
                break

        lines.clear()
        lines.extend(h_lines)
        lines.extend(v_lines)

    def _fix0(self,lines:list[_Line]):

        def fix(lines:list[_Line],is_h:bool):
            h_lines,v_lines = self._split_lines(lines)
            if is_h:
                x0,y0,x1,y1=0,1,2,3
                target_lines = h_lines
            else:
                x0,y0,x1,y1=1,0,3,2
                target_lines = v_lines
            
            if len(target_lines)<2:
                return
            
            target_lines.sort(key=lambda line:line[y1],reverse=True)
            for i,j in [(0,1),(-1,-2)]:
                if len(target_lines)<2:
                    break
                line1=target_lines[i]
                line2=target_lines[j]
                if abs(line1[x0]-line2[x0])<=2 and abs(line1[x1]-line2[x1])<=2 and abs(line1[y1]-line2[y1])<=3:
                    lists.remove(lines,[line2],use_is=True)

        fix(lines,True)
        fix(lines,False)

    def _fix1(self,lines:list[_Line],*,is_h:bool=True):
        """彩色表格的线很难计算"""
        #[---][---][---] 彩色的表头，没有明显的单元格的线
        #     |    |
        #-----|----|------ 后面的行有明显的线
        #-----|----|-----
        debugger = self._degugger.bind()
        h_lines,v_lines = self._split_lines(lines)
        if len(v_lines)<3 or len(h_lines)<3:
            return False
        
        if is_h:
            x0,y0,x1,y1=0,1,2,3
        else:
            x0,y0,x1,y1=1,0,3,2
            v_lines,h_lines=h_lines,v_lines
        #找到对齐的垂直线但是没有水平线的
        v_lines.sort(key=lambda line:line[y1],reverse=True)
        h_lines.sort(key=lambda line:line[y1],reverse=True)
        groups:list[list[_Line]]=[]
        group:list[_Line]=[]
        for v_line in v_lines:
            if not group:
                group=[v_line]
                groups.append(group)
            elif abs(group[0][y1]-v_line[y1])<=2:
                #总是使用group[0]，避免误差累积增大
                group.append(v_line)
            else:
                group=[v_line]
                groups.append(group)
        
        v_lines.sort(key=lambda line:line[x0])
        for group in groups:
            #判断是否有线和这一组相交，至少有一条
            y = min(group,key=lambda line:line[y1])[y1]
            ok=False
            for h_line in h_lines:
                if abs(h_line[y1]-y)<=2:
                    #表示有线相交
                    ok=True
                    break
            if ok:
                continue

            #表示存在垂直线和水平线没有相交的，就需要补充一条水平线？
            #或者直接拉长垂直线？
            group.sort(key=lambda line:line[x0])
            #|       |
            #--------- 补充这条水平线？
            #| | | | |

            left_lines:list[_Line]=[]
            right_lines:list[_Line]=[]
            for line in v_lines:
                if line[y0]<group[0][y1]<=line[y1]:
                    if line[x0]<group[0][x0]:
                        left_lines.append(line)
                    elif line[x0]>group[-1][x0]:
                        right_lines.append(line)
                    else:
                        pass
            
            if left_lines and right_lines:
                left_lines.sort(key=lambda line:line[x0])
                right_lines.sort(key=lambda line:line[x0])
                left_line = left_lines[-1]
                right_line = right_lines[0]
                new_line = [left_line[x0],group[0][y1],right_line[x0],group[0][y1]]
                lines.append(new_line)
                self._logger.warning('可能是因为彩色表格的表头，丢失了水平线，补充一条水平线,page=%s',self._page.number if self._page else None)
                if self._page is not None and debugger.allow('gui'):
                    #self._page.show('fix line',lines=[BBox(new_line)])
                    pass
    

    def _fix2(self,lines:list[_Line]):
        """删除局部的线"""
        #-------------
        #------
        #      |  =>把这条线给删除
        #------
        #-------------
        def is_deleted(v_line:_Line,h_lines:Sequence[_Line],x_i:int)->bool:
            for h_line in h_lines:
                if h_line[1]-v_line[3]>1:
                    #h_lines已经排序过了，从低到高，所以就不需要再继续了
                    break

                if abs(h_line[x_i]-v_line[0])<=1 and (abs(h_line[1]-v_line[3])<=1 or abs(h_line[1]-v_line[1])<=1):
                    return True
                else:
                    pass
            
            return False

        def is_v_edge(v_line:_Line,bbox:BBox,x_i:int)->bool:
            if abs(v_line[3]-bbox[3])<=1 and abs(v_line[1]-bbox[1])<=1 and abs(v_line[0]-bbox[x_i])<=1:
                return True
            else:
                return False
        h_lines,v_lines = self._split_lines(lines)
        h_lines.sort(key=lambda line:line[1])
        v_lines.sort(key=lambda line:line[0])

        
        bbox = BBox.join(lines)
        if len(v_lines)>0 and is_v_edge(v_lines[0],bbox,0):
            del v_lines[0]
        
        if len(v_lines)>0 and is_v_edge(v_lines[-1],bbox,2):
            del v_lines[-1]

        if len(v_lines)==0:
            return
        

        for x_i in [0,2]:
            #|--- or ----|
            deleted_lines = lists.remove2(v_lines,lambda i,objs:is_deleted(objs[i],h_lines,x_i))
            lists.remove(lines,deleted_lines,use_is=True)

    def _fix3(self,lines:list[_Line]):
        """删除悬空的线"""
        #彩色表格，有进度条小矩形的，容易出现
        def is_deleted(line:_Line,lines:Sequence[_Line],is_h:bool)->bool:
            if is_h:
                x0,y0,x1,y1=0,1,2,3
            else:
                x0,y0,x1,y1=1,0,3,2

            #如果是水平线(line)，删除没有和任何垂直线(lines)连接的
            #如果是垂直线(line)，删除没有和任何水平线(lines)连接的

            start_ok=False
            end_ok=False

            #这两个也可以设置为0的
            d1=1
            d2=1
            d3=1
            for line2 in lines:
                if line2[x0]-line[x1]>d1:
                    #已经排序过，允许一点误差
                    break

                if line2[y0]-d2<=line[y0]<=line2[y1]+d2:
                    if abs(line[x0]-line2[x0])<=d3:
                        start_ok=True
                    elif abs(line[x1]-line2[x0])<=d3:
                        end_ok=True
                    else:
                        pass

                    if start_ok and end_ok:
                        break
                
            if start_ok and end_ok:
                #保留
                return False
            else:
                #删除
                return True
            
        h_lines,v_lines = self._split_lines(lines)
        #先删除悬空的水平线，可能有些表格使用进度条，导致解析为悬空的线

        #为了快速，先排序
        h_lines.sort(key=lambda line:line[1])
        v_lines.sort(key=lambda line:line[0])
        while True:
            deleted_h_lines = lists.remove2(h_lines,lambda i,objs:is_deleted(objs[i],v_lines,True))
            lists.remove(lines,deleted_h_lines,use_is=True)
            deleted_v_lines = lists.remove2(v_lines,lambda i,objs:is_deleted(objs[i],h_lines,False))
            lists.remove(lines,deleted_v_lines,use_is=True)
            if len(deleted_h_lines)==0 and len(deleted_v_lines)==0:
                break

        pass


            
