
import logging
import math
from pathlib import Path
from typing import Any, Final, Mapping, Sequence, cast
import PIL
import PIL.Image
import PIL.ImageDraw
import cv2
import numpy as np
import numpy.typing as npt

from memect.base import lists

from memect.base.matrix import Matrix


from .base import KLine, KPage, kPage

from memect.base.bbox import BBox

from memect.base.debug import XDebugger


type ANY_IMAGE=PIL.Image.Image|cv2.typing.MatLike|str|Path
class LineParser:
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def parse(self, any_img: ANY_IMAGE,*,span_bboxes:Sequence[BBox]|None=None,scale:float|None=None) -> list[Any]:
        """
        span_bboxes: 提供了相对图片的字符串的bbox，坐标原点为左上角
        scale：表示表格图片所在原图，相对标准页面（596*842）缩放了多少，如果为None，表示不知道
        """
        debugger=self._debugger.bind()
        images:dict[str,Any]={}

        img = self.load_image(any_img)
        #进行归一化，相对596*842，或者842*596
        if True:
            h,w = img.shape[:2]
            s=1200/w

            img=cv2.resize(img,(int(s*w),int(s*h)))        
        images['original']=img
        if False:
            img = self._remove_stamp_by_color(img)
            images['remove stamp']=img

        if span_bboxes:
            #可以对图片进行inpaint
            #self._inpaint()
            pass

        length_threshold=15
        if span_bboxes:
            span_bboxes=sorted(span_bboxes,key=lambda bbox:bbox.height)
            #获得最小的bbox
            length_threshold = max(15,span_bboxes[0].height+2)
        elif scale is not None:
            length_threshold = int(15*scale)
        else:
            length_threshold=15
        


        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        images['gray']=gray
        # 可选

        blur1 = cv2.GaussianBlur(gray, (3,3), 0)
        images['gaussian blur']=blur1

        #对于斑马线表格效果好
        #blur3 = cv2.medianBlur(gray,3)
        #images['media blur']=blur3

        blur2 = cv2.blur(gray, (3,3))
        images['blur']=blur2

        if False:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
            images['enhanced']=gray



        #thinning_img = cv2.ximgproc.thinning(gray, thinningType=cv2.ximgproc.THINNING_ZHANGSUEN)
        #images['thinning']=thinning_img


        #length_threshod=15
        fld = cv2.ximgproc.createFastLineDetector(length_threshold=length_threshold,     # 最小线长度（默认10）
                                                    distance_threshold=1.41421356,  # 距离阈值（默认√2）
                                                    canny_th1=60,           # Canny低阈值（默认50）
                                                    canny_th2=100,          # Canny高阈值（默认50，建议调高）
                                                    #越大，背景噪音影响就更大（肉眼看不出来的噪音）
                                                    canny_aperture_size=3,  # Canny孔径（3, 5, 7）
                                                    do_merge=True           # 合并相似的线段（默认False）
                                                    )
        

        result_imgs:list[cv2.typing.MatLike]=[]
        spliter = np.zeros((2,img.shape[1]*2,3),np.uint8)
        #黄色为分割线
        spliter[:,:]=(0,255,255)
        lines_list:list[cv2.typing.MatLike]=[]
        for img2 in [blur1,blur2]:
            lines = fld.detect(img2)
            lines_list.append(lines)
            if debugger.allow('info'):
                debugger.print(f'length_threshold={length_threshold},lines={len(lines)}')
            # 绘制检测到的直线
            if debugger.allow('gui'):
                result_img = fld.drawSegments(img, lines,linecolor=(255,0,0),linethickness=2)
                result_imgs.append(np.hstack((cv2.cvtColor(img2,cv2.COLOR_GRAY2BGR),result_img)))
                result_imgs.append(spliter)
        

        all_lines = np.vstack(lines_list)
        if debugger.allow('gui'):
            result_img = fld.drawSegments(img,all_lines,linecolor=(255,0,0),linethickness=2)
            result_imgs.append(np.hstack((img,result_img)))
            images['fld']=np.vstack(result_imgs)
        

        
        if debugger.allow('gui'):
            self._show_images(images)
        
        new_lines = _LineCleaner().clean(all_lines,img=img)
        
    def get_h_lines(self,page:KPage,bbox:BBox,*,objects:list[BBox]|None=None)->list[Line]:
        """获得指定区域的水平线"""
        full_img = page.image
        img = page.crop(bbox)
        
        #box=bbox.transform(m).large.ensure((0,0,w,h)).idata

        #转换为相对截图的坐标
        bboxes:list[BBox]=[]
        if objects:
            #改变原点为左上角
            sw = full_img.width/page.width
            sh = full_img.height/page.height
            #左下角转换为左上角
            x,y = bbox.change_origin(page.height)[0:2]
            m= M(1,0,0,-1,0,page.height).translate((-x,-y)).scale(sw,sh).to_tuple()
            for obj in objects:
                obj = obj.transform(m).small.ensure((0,0,img.width,img.height))
                bboxes.append(obj)
            
        lines = self.get_h_lines2(img,bboxes=bboxes)

        sw=page.width/full_img.width
        sh=page.height/full_img.height
        #line的坐标原点为左上角，先转换为左下角
        m = M(1,0,0,-1,0,img.height).scale(sw,sh).translate((bbox.x0,bbox.y0)).to_tuple()
        new_lines:list[Line]=[]
        for line in lines:
            #把相对图片的坐标转换相对页面
            new_bbox = line.transform(m).trunc.ensure((0,0,page.width,page.height))
            new_line = Line(page,new_bbox)
            new_lines.append(new_line)
        
        #page.show('lines',lines=new_lines)
        return new_lines

    def get_h_lines2(self,any_img:ANY_IMAGE,*,bboxes:Sequence[BBox]|None=None)->list[BBox]:
        """仅仅获得主要的水平线，特别是支持彩色表格，斑马线表格等
        span_bboxes:相对图片的坐标，且原点为左上角
        """
        debugger = self._debugger.bind()
        images:dict[str,cv2.typing.MatLike]={}

        img = self.load_image(any_img)
        original_img = img
        images['original']=original_img
        #归一化为统一的大小
        height, width = img.shape[:2]
        standard_width=1000
        scale:Final= standard_width/width
        img = cv2.resize(img,(int(width*scale),int(height*scale)))
        images['resize']=img
        if bboxes:
            #如果需要先把字符串擦除
            #span_bboxes也需要缩放
            #img = self._inpaint(img,span_bboxes=span_bboxes)
            bboxes = [bbox.scale(scale) for bbox in bboxes]
            if debugger.allow('gui'):
                bboxes_img = img.copy()
                for b in bboxes:
                    cv2.rectangle(bboxes_img,b.idata[0:2],b.idata[2:4],(0,255,255),2)
                images['bboxes']=bboxes_img
            img = self._inpaint(img,bboxes=bboxes)
            images['inpaint']=img
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        images['gray']=gray
        fld = cv2.ximgproc.createFastLineDetector(length_threshold=50,     # 最小线长度（默认10）
                                                    distance_threshold=1.41421356,  # 距离阈值（默认√2）
                                                    canny_th1=50,           # Canny低阈值（默认50）
                                                    canny_th2=100,          # Canny高阈值（默认50，建议调高）
                                                    #越大，背景噪音影响就更大（肉眼看不出来的噪音）
                                                    canny_aperture_size=3,  # Canny孔径（3, 5, 7）
                                                    do_merge=True           # 合并相似的线段（默认False）
                                                    )
        lines = fld.detect(gray)

        if debugger.allow('gui'):
            fld_img = fld.drawSegments(img, lines,linecolor=(255,0,0),linethickness=2)
            images['fld']=fld_img
        
        if debugger.allow('gui'):
            self._show_images(images)
        
        #对线进行清理，仅仅使用水平线
        cleaner = _LineCleaner()
        h_lines = cleaner.clean_h_lines(img,lines)

        #转换为相对原图的坐标
        m = M(1,0,0,1,0,0).scale(1/scale,1/scale).to_tuple()
        return [line.transform(m).trunc for line in h_lines]
    

    def load_image(self,any_img:ANY_IMAGE)->cv2.typing.MatLike:
        img:cv2.typing.MatLike
        if isinstance(any_img,PIL.Image.Image):
            # RGBA => RGB
            img = np.array(any_img.convert('RGB'))
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        elif isinstance(any_img,(str,Path)):
            #bgr or bgra
            #不能够直接使用
            #bgr or bgra => bgr (因为只是简单的去掉a，如果是完全透明的，如：(0,0,0,0) => (0,0,0)，就变成了黑色，多数情况下需要为(255,255,255))
            #cv2.imread(str(any_img),cv2.IMREAD_COLOR)
            cv_img = cv2.imread(str(any_img),cv2.IMREAD_UNCHANGED)
            if cv_img is None:
                raise ValueError(f'文件不存在:{any_img}')
            img=cv_img
        else:
            img=any_img

        if img.shape[2]==4:
            #bgr or bgra => bgr (因为只是简单的去掉a，如果是完全透明的，如：(0,0,0,0) => (0,0,0)，就变成了黑色，多数情况下需要为(255,255,255))
            #img = cv2.cvtColor(img,cv2.COLOR_BGRA2BGR)
            #创建白色背景
            white_bg = np.ones_like(img[:,:,:3]) * 255
            alpha = img[:,:,3:4] / 255.0
            img = (img[:,:,:3] * alpha + white_bg * (1 - alpha)).astype(np.uint8)
        
        return img

    
    def _remove_stamp_by_color(self,img:npt.NDArray[Any],*,method:int=1)->npt.NDArray[Any]:
        """
        img: BGR格式
        """
        #img = cv2.imread(image_path)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        if method==1:
            h=10
            low_range = np.array([0,20,20])
            high_range=np.array([0+h,255,255])
            mask1 = cv2.inRange(hsv,low_range,high_range)
            low_range = np.array([180-h,20,20])
            high_range = np.array([179,255,255])
            mask2 = cv2.inRange(hsv,low_range,high_range)
            mask = cv2.bitwise_or(mask1,mask2)
        else:
            # 定义印章颜色的HSV范围（需要根据实际印章颜色调整）
            lower_red = np.array([0, 50, 50])
            upper_red = np.array([10, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red, upper_red)
            
            lower_red = np.array([170, 50, 50])
            upper_red = np.array([180, 255, 255])
            mask2 = cv2.inRange(hsv, lower_red, upper_red)
            
            mask = cv2.bitwise_or(mask1, mask2)

        result = cv2.inpaint(img, mask,5, cv2.INPAINT_TELEA)
        if False:
            cv2.imshow('remove stamp',result)
            cv2.waitKey()
            cv2.destroyAllWindows()
        return result
    
    def _inpaint(self,img:cv2.typing.MatLike,*,text_bboxes:Sequence[BBox]|None=None,bboxes:Sequence[BBox]|None=None,polys:Sequence[Sequence[Any]]|None=None)->cv2.typing.MatLike:


        debug=False

        def ensure(bbox:BBox,size:tuple[float,float])->BBox:
            x0,y0,x1,y1=bbox.data
            x0=max(0,x0)
            x1=min(size[0],x1)
            y0=max(0,y0)
            y1=min(size[1],y1)
            return bbox.copy(x0=x0,x1=x1,y0=y0,y1=y1)

        def is_dark_bg(image:cv2.typing.MatLike,*,margin_size:int=2,threshold:float=0.6,min_size:tuple[int,int]=(5,5))->bool:
            """判断是否为深色背景"""
            #如果为深色背景，二值化后，就是白底黑字，所以只需要判断是否为白底
            #中文笔画多占用的空间多，所以仅仅考虑4周的空间如果白色居多的
            h,w = image.shape[:2]
            if h<min_size[1] or w<min_size[0]:
                return False
            image = image.copy()
            left=image[:,0:margin_size].reshape(-1,1)
            right=image[:,-margin_size:0].reshape(-1,1)
            top=image[0:margin_size,:].reshape(-1,1)
            bottom=image[-margin_size:0,:].reshape(-1,1)
            margin_image=np.concatenate((left,right,top,bottom))
            #获得背景像素数（白色）
            n=cv2.countNonZero(margin_image)
            total=margin_image.shape[0]
            if False:
                print('==>',image.shape,margin_image.shape,total,n,n/total)
                cv2.imshow('margin',margin_image)
                cv2.waitKey()
                cv2.destroyAllWindows()
            return n/total>=threshold
        def to_binary(img:cv2.typing.MatLike)->Any:
            gray = cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
            #黑底白字
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            if is_dark_bg(thresh):
                thresh=cv2.bitwise_not(thresh)
            return thresh
        
        m=None
        mask = cast(npt.NDArray[np.uint8],np.zeros(img.shape[:2], dtype=np.uint8))
        
        image_size = (img.shape[1],img.shape[0])
        if text_bboxes:
            for bbox in text_bboxes:
                if m is not None:
                    bbox = bbox.transform(m.to_tuple()).small
                    bbox = ensure(bbox,image_size)
                x0,y0,x1,y1 = bbox.idata
                mask[y0:y1,x0:x1]=255
                #TODO 根据轮廓来填充，效果不是很好，因为轮廓可能细了一些，而且周边的颜色也不一定匹配
                if False:
                    x0,y0,x1,y1 = bbox.idata
                    thresh = to_binary(img[y0:y1,x0:x1])
                    text_mask = np.zeros(img.shape[:2],dtype=np.uint8)
                    
                    # 创建不同形状的核
                    # 矩形核
                    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                    # 椭圆核
                    #kernel_ellipse = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
                    # 十字核
                    #kernel_cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (5, 5))

                    dilated = cv2.dilate(thresh, kernel, iterations=1)
                    text_mask[y0:y1,x0:x1]=dilated
                    cv2.imshow('thresh',thresh)
                    cv2.imshow('dilated',dilated)
                    cv2.imshow('text mask',text_mask)

                    #获得0区域的平均值
                    #黑底白字=>白底黑字
                    b,g,r=cv2.mean(img[y0:y1,x0:x1],mask=cv2.bitwise_not(dilated))[:3]
                    #或者获得出现最多次数的元素？
                    bgr=(int(b),int(g),int(r))

                    img[text_mask!=0]=bgr
                    print('==.bgr',bgr)
                    cv2.imshow('imgxx',img)
                    cv2.waitKey()
                    cv2.destroyAllWindows()

                if False:
                    coords = cv2.findNonZero(thresh)
                    x, y, w, h = cv2.boundingRect(coords)
                    text_mask = np.zeros_like(thresh)
                    text_mask[y:y+h,x:x+w]=255
                    mask[y0:y1,x0:x1]=text_mask
                    #cv2.rectangle(mask,(x0,y0),(x1,y1),255,-1)
                    #print('bbox',bbox)
                
        if bboxes:
            for bbox in bboxes:
                #必须为原点为左上角，相对图片
                if m is not None:
                    bbox = bbox.transform(m.to_tuple()).large
                    bbox = ensure(bbox,image_size)
                x0,y0,x1,y1 = bbox.idata
                mask[y0:y1,x0:x1]=255
                #cv2.rectangle(mask,(x0,y0),(x1,y1),255,-1)
                #print('bbox',bbox)
                
        
        if polys:
            for points in polys:
                if m is not None:
                    points = list(m.transforms(points))
                pts = np.array(points, dtype=np.int32)
                cv2.fillPoly(mask, [pts], 255)

        #cv2.INPAINT_TELEA: Fast, based on Fast Marching Method
        #cv2.INPAINT_NS: Slower but sometimes better quality
        #t1 = time.monotonic()
        new_img = cv2.inpaint(img, mask, inpaintRadius=3, flags=cv2.INPAINT_NS)
        #t2 = time.monotonic()
        #print('==>>',t2-t1)
        if debug:
            cv2.imshow('mask',mask)
            cv2.imshow('img',img)
            cv2.imshow('final img',new_img)
            cv2.waitKey()
            cv2.destroyAllWindows()

        return new_img

    def _show_images(self,images:Mapping[str,Any]):
        for title,image in images.items():
            cv2.imshow(title,image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    

class _LineCleaner:
    _logging = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()
    
    def clean(self,lines:cv2.typing.MatLike,*,bboxes:Sequence[BBox]|None=None,img:cv2.typing.MatLike|None=None):
        #如果是合并水平线和垂直线，可以使用简单的算法
        #如果需要支持斜线（如：表格倾斜，算法就稍微复杂了）

        debugger = self._debugger.bind()
        images:dict[str,cv2.typing.MatLike]={}
        new_lines:list[BBox]=[]
        for line in lines:
            x0,y0,x1,y1 = map(float,line[0])
            x0,x1=min(x0,x1),max(x0,x1)
            y0,y1=min(y0,y1),max(y0,y1)
            bbox=BBox(x0,y0,x1,y1)
            new_lines.append(bbox)

        if debugger.allow('gui') and img is not None:
            images['init']=self._draw_lines(img,(new_lines,(0,255,255)))
        #线划分水平线和垂直线
        h_lines,v_lines = self._split_lines(new_lines)
        if debugger.allow('gui') and img is not None:
            images['split']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)))

        if bboxes:
            for bbox in bboxes:
                bbox.adjust(dx=1,dy=1).remove(h_lines)
                bbox.adjust(dx=1,dy=1).remove(v_lines)
            if debugger.allow('gui') and img is not None:
                images['remove by spans']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)))

        lists.remove2(h_lines,lambda i,objs:objs[i].width<=2)
        lists.remove2(v_lines,lambda i,objs:objs[i].height<=2)

        #不需要标准处理，先快速合并重叠的，因为可能线比较粗
        h_lines = self._quick_merge(h_lines,is_h=True,dx=3,dy=1)
        v_lines = self._quick_merge(v_lines,is_h=False,dx=1,dy=3)
        if debugger.allow('gui') and img is not None:
            images['quick merge']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)))

        h_lines = self._normalize_lines(h_lines,is_h=True)
        v_lines = self._normalize_lines(v_lines,is_h=False)
        if debugger.allow('gui') and img is not None:
            images['normalized']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)))

        h_lines = self._merge_lines(h_lines,is_h=True,d1=5)
        v_lines = self._merge_lines(v_lines,is_h=False)
        h_lines=[line.trunc for line in h_lines]
        v_lines=[line.trunc for line in v_lines]
        if debugger.allow('gui') and img is not None:
            images['merge1']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)))
        
        if False:
            h_lines = self._merge_lines(h_lines,is_h=True,d1=5)
            v_lines = self._merge_lines(v_lines,is_h=False)
            h_lines=[line.trunc for line in h_lines]
            v_lines=[line.trunc for line in v_lines]
            if debugger.allow('gui') and img is not None:
                images['merge2']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)))

        h_lines,v_lines,fixed_lines = self._fix1(h_lines,v_lines)
        if debugger.allow('gui') and img is not None:
            images['fix1']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)),(fixed_lines,(0,0,255)))
        
        h_lines,v_lines,fixed_lines = self._fix2(h_lines,v_lines)
        if debugger.allow('gui') and img is not None:
            images['fix2']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)),(fixed_lines,(0,0,255)))

        from ._line import Liner
        assert img is not None
        h = img.shape[0]
        lines2:list[Any]=[]
        for line in h_lines+v_lines:
            line = line.change_origin(h)
            lines2.append(line.to_list())
        lines2  = Liner().parse(lines2)
        lines3:list[BBox]=[]
        for line in lines2:
            x0,y0,x1,y1=line
            y0,y1 = h-y1,h-y0
            lines3.append(BBox(x0,y0,x1,y1))
        
        h_lines,v_lines = self._split_lines(lines3)
        if debugger.allow('gui') and img is not None:
            images['clean']=self._draw_lines(img,(h_lines,(0,255,255)),(v_lines,(255,0,0)),use_pil=True)

        if debugger.allow('gui') and img is not None:
            self._show_images(images)

        return h_lines+v_lines

    def clean_h_lines(self,img:cv2.typing.MatLike,lines:cv2.typing.MatLike,*,bboxes:Sequence[BBox]|None=None):
        debugger = self._debugger.bind()
        images:dict[str,cv2.typing.MatLike]={}
        new_lines:list[BBox]=self._convert_lines(lines)

        images['img']=img
        if debugger.allow('gui'):
            images['init lines']=self._draw_lines(img,(new_lines,(0,255,255)))
        #线划分水平线和垂直线
        h_lines,_ = self._split_lines(new_lines)
        if debugger.allow('gui'):
            images['split']=self._draw_lines(img,(h_lines,(0,255,255)))

        if bboxes:
            for bbox in bboxes:
                bbox.adjust(dx=1,dy=1).remove(h_lines)
                #bbox.adjust(dx=1,dy=1).remove(v_lines)
            if debugger.allow('gui'):
                images['remove by spans']=self._draw_lines(img,(h_lines,(0,255,255)))

        lists.remove2(h_lines,lambda i,objs:objs[i].width<=2)
        #lists.remove2(v_lines,lambda i,objs:objs[i].height<=2)

        #不需要标准处理，先快速合并重叠的，因为可能线比较粗
        h_lines = self._quick_merge(h_lines,is_h=True,dx=3,dy=1)
        #v_lines = self._quick_merge(v_lines,is_h=False,dx=1,dy=3)
        if debugger.allow('gui'):
            images['quick merge']=self._draw_lines(img,(h_lines,(0,255,255)))

        h_lines = self._normalize_lines(h_lines,is_h=True)
        if debugger.allow('gui'):
            images['normalized']=self._draw_lines(img,(h_lines,(0,255,255)))

        h_lines = self._merge_lines(h_lines,is_h=True,d1=5)
        h_lines=[line.trunc for line in h_lines]
        if debugger.allow('gui'):
            images['merge']=self._draw_lines(img,(h_lines,(0,255,255)))
        
        #对于间距大一些的，也可以连接在一起，因为主要是给无边框表格调整位置更美观实用的，即使错误的连接也可以不影响
        old_h_lines = h_lines
        h_lines = self._connect_lines(h_lines,is_h=True)
        if debugger.allow('gui'):
            images['connect']=self._draw_lines(img,(h_lines,(0,255,255)),(self._get_diff_lines(old_h_lines,h_lines),(0,0,255)))
        
        if True:
            old_h_lines = h_lines
            h_lines = self._align_lines(h_lines,is_h=True)
            if debugger.allow('gui'):
                images['align']=self._draw_lines(img,(h_lines,(0,255,255)),(self._get_diff_lines(old_h_lines,h_lines),(0,0,255)))
        
        if True:
            old_h_lines = h_lines
            h_lines = self._remove_ms_lines(h_lines,is_h=True)
            if debugger.allow('gui'):
                images['remove ms']=self._draw_lines(img,(h_lines,(0,255,255)),(self._get_removed_lines(old_h_lines,h_lines),(0,0,255)))
            
        if debugger.allow('gui'):
            images['final']=self._draw_lines(img,(h_lines,(0,255,255)))
            self._show_images(images)
        return h_lines
    
    def _get_diff_lines(self,old_lines:Sequence[BBox],new_lines:Sequence[BBox])->list[BBox]:
        """获得新增加的线"""
        #return list(set(old_lines) ^ set(new_lines) )
        result:list[BBox]=[]
        for line in new_lines:
            if line not in old_lines:
                result.append(line)
        return result
    
    def _get_removed_lines(self,old_lines:Sequence[BBox],new_lines:Sequence[BBox])->list[BBox]:
        """获得删除的线"""
        #return list(set(old_lines) ^ set(new_lines) )
        result:list[BBox]=[]
        for line in old_lines:
            if line not in new_lines:
                result.append(line)
        return result
    
    def _convert_lines(self,lines:cv2.typing.MatLike)->list[BBox]:
        new_lines:list[BBox]=[]
        for line in lines:
            x0,y0,x1,y1 = map(float,line[0])
            x0,x1=min(x0,x1),max(x0,x1)
            y0,y1=min(y0,y1),max(y0,y1)
            bbox=BBox(x0,y0,x1,y1)
            new_lines.append(bbox)
        return new_lines
     
    def _show_images(self,images:Mapping[str,Any]):
        for title,image in images.items():
            cv2.imshow(title,image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    def _draw_lines(self,img:cv2.typing.MatLike,*lines_list:tuple[Sequence[BBox],tuple[int,int,int]],use_pil:bool=False)->cv2.typing.MatLike:
        if use_pil:
            pil_img = PIL.Image.fromarray(cv2.cvtColor(img,cv2.COLOR_BGR2RGB))
            draw = PIL.ImageDraw.Draw(pil_img)
            for lines,color in lines_list:
                b,g,r = color
                color = (r,g,b)
                for line in lines:
                    draw.line(line.idata,fill=color,width=2)
            img = np.array(pil_img)
            img = cv2.cvtColor(img,cv2.COLOR_RGB2BGR)
        else:
            img = img.copy()
            for lines,color in lines_list:
                for line in lines:
                    cv2.line(img,line.idata[0:2],line.idata[2:4],color,thickness=2)
        return img

    def _split_lines(self,lines:list[BBox],*,angle_threshold:float=10)->tuple[list[BBox],list[BBox]]:
        h_lines:list[BBox] = []
        v_lines:list[BBox] = []
        other_lines:list[BBox] = []
        
        for line in lines:
            x1, y1, x2, y2 = line
            
            # 计算角度（-90到90度）
            dx = x2 - x1
            dy = y2 - y1
            
            if dx == 0:  # 垂直线
                angle = 90
            else:
                angle = math.degrees(math.atan2(dy, dx))
            
            # 归一化到0-180度
            angle = angle % 180
            
            # 分类
            if angle < angle_threshold or angle > 180 - angle_threshold:
                # 近似水平线（0度或180度）
                h_lines.append(line)
            elif abs(angle - 90) < angle_threshold:
                # 近似垂直线（90度）
                v_lines.append(line)
            else:
                other_lines.append(line)
        return h_lines,v_lines
    
    def _normalize_lines(self,lines:list[BBox],*,is_h:bool)->list[BBox]:
        new_lines:list[BBox]=[]
        for line in lines:
            x0,y0,x1,y1=line.data
            if is_h:
                y0=y1=line.cy
            else:
                x0=x1=line.cx
            line = BBox(x0,y0,x1,y1)
            new_lines.append(line)
        return new_lines

    def _split_groups(self,lines:list[BBox],*,is_h:bool,threshold:float=5)->list[list[BBox]]:
        """
        把线根据阀值分组
        lines：
        is_h:
        threshold: 如果是水平线，就是表示线的垂直间距，如果是垂直线，就是线的水平间距
        """
        if not lines:
            return []
        
        if is_h:
            x0,y0,x1,y1=0,1,2,3
        else:
            x0,y0,x1,y1=1,0,3,2
        
        lines = sorted(lines,key=lambda line:line[y0])
        groups:list[list[BBox]]=[]
        group:list[BBox]=[lines[0]]
        groups.append(group)
        for line in lines[1:]:
            #使用group[-1]会累积误差，使用group[0]
            if line[y0]-group[-1][y0]<=threshold:
                #[--group[0]--]
                #[--line--]
                group.append(line)
            else:
                group=[line]
                groups.append(group)
        return groups


    def _quick_merge(self,lines:list[BBox],*,is_h:bool=True,dx:float=3,dy:float=1)->list[BBox]:
        debugger = self._debugger.bind()
        if is_h:
            x0,y0,x1,y1=0,1,2,3
        else:
            x0,y0,x1,y1=1,0,3,2
        
        old_lines = lines
        lines = sorted(lines,key=lambda line:line[x1]-line[x0],reverse=True)
        new_lines:list[BBox]=[]
        while lines:
            line = lines.pop(0)
            i=0
            while i<len(lines):
                line2 = lines[i]
                if line2[x0]<=line[x1]+dx and line2[x1]>=line[x0]-dx and line2[y0]<=line[y1]+dy and line2[y1]>=line[y0]-dy:
                    line = line.union([line2])
                    del lines[i]
                else:
                    i+=1
            new_lines.append(line)
        
        if debugger.allow('info'):
            debugger.print(f'quick merge,is_h={is_h},from={len(old_lines)},to={len(new_lines)}')
        return new_lines
            
    def _merge_lines(self,lines:list[BBox],is_h:bool=True,min_length:float=2,d1:float=5,d2:float=3)->list[BBox]:
        #这里坐标原点为左上角
        debugger = self._debugger.bind()
        groups = self._split_groups(lines,is_h=is_h,threshold=d1)
        
        new_lines:list[BBox]=[]
        for group in groups:
            group = self._merge_group(group,is_h=is_h,d=d2)
            new_lines.extend(group)
        
        if debugger.allow('info'):
            debugger.print(f'merge lines,is_h={is_h},from={len(lines)},to={len(new_lines)}')
        return new_lines



    def _merge_group(self,lines:list[BBox],*,is_h:bool,d:float=3)->list[BBox]:
        if is_h:
            x0,y0,x1,y1=0,1,2,3
        else:
            x0,y0,x1,y1=1,0,3,2
        
        #长度排序，更好一些，虽然需要多计算几次
        lines = sorted(lines,key=lambda line:line[x1]-line[x0],reverse=True)
        #lines = sorted(lines,key=lambda line:line[x0])

        new_lines:list[BBox]=[]
        while lines:
            line = lines.pop(0)
            i=0
            while i<len(lines):
                line2 = lines[i]
                #[--line--][--line2--]
                if line2[x0]<=line[x1]+d and line2[x1]>=line[x0]-d:
                    line = line.union([line2])
                    del lines[i]
                else:
                    i+=1
            
            new_lines.append(line)
        
        new_lines = self._normalize_lines(new_lines,is_h=is_h)
        return new_lines
    

    def _connect_lines(self,lines:list[BBox],*,is_h:bool,d1:float=3,d2:float=30,min_length:float=100)->list[BBox]:
        """把断开的线连接
        d1: 如果是水平线，表示垂直间距，如果是垂直线，表示水平间距
        d2: 如果是水平线，表示水平距离在这个范围内可以连接
        min_length: 表示连接的线的最小长度
        """
        if is_h:
            x0,y0,x1,y1=0,1,2,3
        else:
            x0,y0,x1,y1=1,0,3,2
        
        groups = self._split_groups(lines,is_h=is_h,threshold=d1)
        for group in groups:
            if len(group)==1:
                continue
            group.sort(key=lambda line:line[x0])
            i=1
            while i<len(group):
                line1 = group[i-1]
                line2 = group[i]
                #print('===>',line1,line2,line2[x0]-line1[x1])
                if line2[x0]-line1[x1]<=d2 and line1[x1]-line1[x0]>=min_length and line2[x1]-line2[x0]>=min_length:
                    line = line1.union([line2]).as_line()
                    assert line is not None
                    group[i-1]=line
                    del group[i]
                else:
                    i+=1
        
        return lists.flat(groups)
    
    def _align_lines(self,lines:Sequence[BBox],*,is_h:bool)->list[BBox]:
        """对齐线，仅仅对齐长度一致的"""
        if is_h:
            x0,y0,x1,y1=0,1,2,3
        else:
            x0,y0,x1,y1=1,0,3,2
        

        lines = sorted(lines,key=lambda line:line[x1]-line[x0],reverse=True)

        groups:list[list[BBox]]=[]
        while lines:
            line = lines.pop(0)
            group:list[BBox]=[]
            group.append(line)

            length = line[x1]-line[x0]
            if length<=10:
                d=1
            elif length<=50:
                d=2
            elif length<=100:
                d=3
            elif length<=200:
                d=6
            elif length<=500:
                d=10
            elif length<=800:
                d=15
            else:
                d=20
            
            i=0
            while i<len(lines):
                line2 = lines[i]
                #line2比line的长度要小，为了避免层层误差，这里仅仅使用最长的
                if abs(line[x0]-line2[x0])<=d and abs(line[x1]-line2[x1])<=d:
                    group.append(line2)
                    del lines[i]
                else:
                    i+=1
            

            if len(group)>1:
                min_value=min(line[x0] for line in group)
                max_value=max(line[x1] for line in group)
                for j in range(len(group)):
                    group[j]=group[j].set((x0,min_value),(x1,max_value))
            
            groups.append(group)
        
        return lists.flat(groups)
            
    def _remove_ms_lines(self,lines:Sequence[BBox],*,is_h:bool,d1:float=5,d2:float=15,min_length:float=100)->list[BBox]:
        """因为word生成的彩色表格，可能存在很细的边界线，倒置出现不需要的线，这里尝试删除"""
        if is_h:
            x0,y0,x1,y1=0,1,2,3
            axis='h'
        else:
            x0,y0,x1,y1=1,0,3,2
            axis='v'

        
        
        lines = sorted(lines,key=lambda line:line[y0])
        i=1
        while i<len(lines):
            line1 = lines[i-1]

            ok=False
            for j in range(i,len(lines)):
                line2 = lines[j]
                if line2[y0]-line1[y0]>d2:
                    #按y排序，过大就不需要再循环
                    break
                if abs(line1[x0]-line2[x0])<=d1 and abs(line1[x1]-line2[x1])<=d1:
                    #如果是对齐的，误差小一些
                    if line2[y0]-line1[y0]<=10:
                        del lines[i-1]
                        ok=True
                    else:
                        j+=1
                elif line2[y0]-line1[y0]<=d2:
                    if line2[x1]-line2[x0]>=min_length and line2[x0]-d1<=line1[x0] and line1[x1]<=line2[x1]+d1:
                        #   --line1--
                        #-----line2-----
                        del lines[i-1]
                        ok=True
                    elif line1[x1]-line1[x0]>=min_length and line1[x0]-d1<=line2[x0] and line2[x1]<=line1[x1]+d1:
                        #-----line1----
                        #  ---line2--
                        del lines[j]
                        ok=True
                    else:
                        j+=1
                else:
                    j+=1
                
                if ok:
                    break
            
            if not ok:
                i+=1
        
        return lines

    def _fix1(self,h_lines:Sequence[BBox],v_lines:Sequence[BBox])->tuple[list[BBox],list[BBox],list[BBox]]:
        """有些情况，因为文字和线粘连，导致线仅仅识别了部分，处理断头线"""
        #这里使用的坐标原点为左上角
        #对于这种情况，就需要修复，如：
        #--|-|--  |----      =>前面的线穿过垂直线，但是缺少了一部分，这种情况是可以修复的


        def fix_line(index:int,h_lines:list[BBox],v_lines:list[BBox],*,is_h:bool,d1:float=3,d2:float=5):
            """
            
            d1: 如果是修正水平线，表示和垂直线的误差，在这个误差内，认为是连接在一起，如果是修正垂直线，表示和水平线的误差

            """
            if is_h:
                #表示修正水平线
                x0,y0,x1,y1=0,1,2,3
                lines1 = v_lines
                lines2 = h_lines
            else:
                #表示修正垂直线
                x0,y0,x1,y1=1,0,3,2
                lines1 = h_lines
                lines2 = v_lines
            
            
            cross_lines:list[BBox]=[]
            line = lines2[index]
            for line1 in lines1:
                #|   |          |
                #|---|--(line)  |
                #|   |          |
                if line1[y0]-d1<=line[y0]<=line1[y1]+d1:
                    cross_lines.append(line1)
            
            cross_lines.sort(key=lambda line:line[x0])
            i=0
            for i,line1 in enumerate(cross_lines):
                d = line1[x0]-line[x0]
                if d>=d2 and line[x1]-line1[x0]>=0 and i-1>=0:
                    #| |  |        
                    #| | -|--(line) 
                    #| |  |        
                    #    line1
                    #print('===>set line y0',cross_lines[i-1],line1,line,line.set(x0,cross_lines[i-1][x0]))
                    line = line.set((x0,cross_lines[i-1][x0]))

                if d>=0:
                    break

            cross_lines.sort(key=lambda line:line[x1],reverse=True)
            i=0
            for i,line1 in enumerate(cross_lines):
                d = line[x1]-line1[x0]
                if d>=d2 and line1[x0]-line[x0]>=0 and i-1>=0:
                        #| |    |         |
                        #| | ---|--(line) |
                        #| |    |         |
                        #      line1
                    #print('===>set line y1',cross_lines[i-1],line1,line,line.set(x1,cross_lines[i-1][x0]) )
                    line = line.set((x1,cross_lines[i-1][x0]))  
                                  
                if d>=0:
                    break
            
            #表示使用了新的线
            if lines2[index] is not line:
                lines2[index]=line 
                return True
            else:
                return False
        
        old_h_lines = h_lines
        old_v_lines = v_lines
        h_lines=list(h_lines)
        v_lines=list(v_lines)
        if True:
            for i in range(len(v_lines)):
                fix_line(i,h_lines,v_lines,is_h=False)
        
        if True:
            for i in range(len(h_lines)):
                fix_line(i,h_lines,v_lines,is_h=True)
        
        fixed_lines = h_lines+v_lines
        lists.remove(fixed_lines,old_h_lines,use_is=True,strict=False)
        lists.remove(fixed_lines,old_v_lines,use_is=True,strict=False)
        return h_lines,v_lines,fixed_lines

    def _fix2(self,h_lines:Sequence[BBox],v_lines:Sequence[BBox])->tuple[list[BBox],list[BBox],list[BBox]]:
        """有些彩色表格，使用很粗的白色线，可能被识别为2条，合并为一条"""
        #这里使用的坐标原点为左上角
        #对于这种情况，就需要修复，如：
        #--|-|--  |----      =>前面的线穿过垂直线，但是缺少了一部分，这种情况是可以修复的


        def fix_lines(lines:list[BBox],*,is_h:bool,d1:float=3,d2:float=4,min_length:float=50,strict:bool=True):
            """
            
            d1: 如果是水平线，表示左右对齐的误差，如果是垂直线，表示上下对齐的误差
            d2: 如果是水平线，表示两条线之间的垂直间距，如果是垂直线，表示水平间距
            min_length: 表示需要处理合并的线段的最小长度
            strict：True表示仅仅合并两条连续的，False合并满足条件的，不一定需要连续，如：可能水平断开为几节

            """
            if is_h:
                #表示修正水平线
                x0,y0,x1,y1=0,1,2,3

            else:
                #表示修正垂直线
                x0,y0,x1,y1=1,0,3,2

            
            lines.sort(key=lambda line:line[y0])
            i=0
            while i<len(lines)-1:
                line1 = lines[i]
                if line1[x1]-line1[x0]<min_length:
                    i+=1
                    continue

                for k in range(i+1,len(lines)):
                    line2 = lines[k]
                    print('=========>',line1,line2,line2[y0]-line1[y0]<=d2,abs(line1[x0]-line2[x0])<=d1 and abs(line1[x1]-line2[x1])<=d1)
                    if line2[y0]-line1[y0]<=d2 and abs(line1[x0]-line2[x0])<=d1 and abs(line1[x1]-line2[x1])<=d1:
                        
                        #-------line1------
                        #-------line2------
                        new_line = line1.union([line2]).as_line()
                        assert new_line is not None
                        lines[i]=new_line.trunc
                        del lines[k]
                        break
                    elif strict:
                        #必须为连续的情况
                        break
                    else:
                        #不需要为严格的，继续寻找
                        pass
                
                i+=1
                

        
        old_h_lines = h_lines
        old_v_lines = v_lines
        h_lines=list(h_lines)
        v_lines=list(v_lines)
        
        fix_lines(h_lines,is_h=True,strict=False,d2=8)
        fix_lines(v_lines,is_h=False,strict=False,d2=8)
        
        fixed_lines = h_lines+v_lines
        lists.remove(fixed_lines,old_h_lines,use_is=True,strict=False)
        lists.remove(fixed_lines,old_v_lines,use_is=True,strict=False)
        return h_lines,v_lines,fixed_lines
        
    


