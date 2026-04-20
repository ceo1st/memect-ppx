
import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Final, Mapping, cast

import cv2
import freetype
import numpy as np
import PIL
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
from PIL import ImageFont

from memect.base.bbox import BBox
from memect.base.debug import XDebugger

type RGB=tuple[int,int,int]

"""
serif：衬线字体，如：宋体，思源宋体
sans-serif：无衬线字体，如：微软雅黑（Microsoft YaHei），思源黑体（Source Han Sans CN）
monospace：等宽字体，如：Courier New
"""

"""
宋体在PPT中通常不作为正文主体字体，而是作为标题、点睛、营造氛围的辅助字体
尽量避免在PPT中使用传统“宋体(SimSun)”，Windows系统最古老的屏幕宋体，是为点阵打印时代设计的。在PPT中是大忌
思源宋体 (Source Han Serif)

华文宋体 (STSong)在Mac上制作且主要在Mac生态下播放的PPT，可用于大标题或引言，mac系统预装了
serif：宋体（SimSun），思源宋体
sans-serif：黑体(SimHei)，等线 (DengXian)，微软雅黑（Microsoft YaHei），思源黑体（Source Han Sans CN）
            简体中文（CN），繁体中文（TW），CJK（中日韩）（TC）

简单的说：
宋体可以作为标题，且字号一定要大，如：>36

总结：
因为windows/mac系统不一定自带合适的宋体，如果需要使用宋体，如：思源宋体，可以嵌入在pptx文件中，因为是免费的

黑体就选择使用：微软雅黑（windows自带），mac没有，所以会自动选择mac对应的字体（PingFang SC）
             反之，如果使用了mac的字体，windows没有，也会自动选择替代的字体

如果需要支持windows/mac/linux等看起来都一致，就使用思源宋体，思源黑体，且嵌入，
或者：HarmonyOS Sans（鸿蒙黑体）



"""
class FontInfo2:
    def __init__(self,cn_name:str|None,en_name:str|None,data:bytes,*,bold:bool=False,serif:bool=True):
        super().__init__()
        #self.font:Final=font
        assert cn_name or en_name
        self.name:Final[str]= cast(str,cn_name or en_name)
        self.cn_name:Final= cn_name
        self.en_name:Final= en_name

        self.bold:Final=bold
        
        self._cache:dict[int,ImageFont.FreeTypeFont]={}
        self._data:Final[bytes]=data

        self.serif:Final[bool]=serif
    
    
    def truetype(self,size:int=32)->ImageFont.FreeTypeFont:
        font = self._cache.get(size)
        if font is None:
            font = ImageFont.truetype(BytesIO(self._data),size=size)
            self._cache[size]=font
        return font


type _AnyImage=cv2.typing.MatLike|str|Path|PIL.Image.Image

#type _Image = npt.NDArray[np.uint8]
type _Image = cv2.typing.MatLike


@dataclass(kw_only=True)
class FontInfo:
    family:str=''
    serif:bool=True
    bold:bool=False
    color:RGB=(0,0,0)
    bg_color:RGB=(255,255,255)
    score:float=0
    face:freetype.Face
    font:PIL.ImageFont.FreeTypeFont

    #暂时如此了
    parser:'FontParser'

    def get_fontbbox(self,text:str,fontsize:int)->BBox:
        font=self.parser._get_font(serif=self.serif,bold=self.bold,size=fontsize)
        x0,y0,x1,y1=font.font.getbbox(text)
        return BBox(x0,y0,x1,y1)
    
    def get_fontsize(self,text:str,bbox:BBox)->int:
        #可以获得字符的outline的bbox
        #然后就可以计算使用多大的字体可以达到现在的高度
        
        #总是使用偶数
        fontsize=int(bbox.height)//2*2
        if fontsize>20:
            fontsize=(fontsize+3)//4*4
        while True:
            #TODO 暂时如此，后续修改一下代码
            font=self.parser._get_font(serif=self.serif,bold=self.bold,size=fontsize)
            x0,y0,x1,y1=font.font.getbbox(text)
            #print('===>',bbox.height,int(bbox.height)//2*2,fontsize,(x0,y0,x1,y1),bbox,font.font.getmetrics())
            #TODO 主要是考虑宽度，如果宽度够，字体大些也可以书写的下去
            if fontsize<=16:
                dy=3
            else:
                dy=1
            
            if fontsize<=10:
                dx=2
            else:
                dx=5
            if (y1-y0<=bbox.height+dy and x1-x0<=bbox.width+dx):
                break

            if fontsize<=4:
                break
            if fontsize>=24:
                #按常见的，是递减4
                fontsize-=2
            elif fontsize>=10:
                fontsize-=2
            else:
                fontsize-=0.5
        return fontsize
    
    
class FontParser:
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()

        self._font_cache:dict[str,PIL.ImageFont.FreeTypeFont]={}
        self._face_cache:dict[str,freetype.Face]={}
        
    def _show_images(self,images:Mapping[str,_Image],*,wait:bool=True):
        for i,(title,image) in enumerate(images.items()):
            cv2.imshow(f'{i+1}-{title}',image)
        
        if wait:
            cv2.waitKey()
            cv2.destroyAllWindows()

    def _is_dark_bg(self,image:_Image,*,margin_size:int=2,threshold:float=0.6,min_size:tuple[int,int]=(5,5))->bool:
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

    def _crop(self,image:_Image,*,min_size:tuple[int,int]=(5,5))->_Image:
        """黑底白字，去掉四周留空"""
        coords = cv2.findNonZero(image)
        x, y, w, h = cv2.boundingRect(coords)
        if w<min_size[0] or h<min_size[1]:
            return image
        return image[y:y+h,x:x+w]
            
    def to_cv2_image(self,image:_AnyImage)->_Image:
        if isinstance(image,(str,Path)):
            #可以为None
            image2=cv2.imread(str(image))
            if image2 is None:
                raise ValueError(f'不是图片')
            return image2
        elif isinstance(image,PIL.Image.Image):
            image = image.convert('RGB')
            image = np.array(image)
            return cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
        else:
            return image


    def _get_font(self,*,serif:bool=False,bold:bool=False,size:float=64)->FontInfo:
        from .fonts import get_font_dir
        key=f'{"serif" if serif else "sans"}-{"bold" if bold else "regular"}-{size}'
        if serif:
            path=Path(get_font_dir(),f'serif/SourceHanSerif-{"Bold" if bold else "Regular"}.ttc').absolute()
            index=2
        else:
            path=Path(get_font_dir(),f'sans-serif/SourceHanSans-{"Bold" if bold else "Regular"}.ttc').absolute()
            index=2
        
        if key not in self._font_cache:
            font = PIL.ImageFont.truetype(path,size=size,index=index)
            self._font_cache[key]=font

        #这个不需要size
        key2=f'{"serif" if serif else "sans"}-{"bold" if bold else "regular"}'
        if key2 not in self._face_cache:
            self._face_cache[key2]=freetype.Face(str(path),index=index)
        
        font = self._font_cache[key]
        face = self._face_cache[key2]
        family,_ = font.getname()
        assert family is not None
        return FontInfo(parser=self,family=family,serif=serif,bold=bold,face=face,font=font)
    

    def _get_fonts(self,size:int)->list[FontInfo]:
        fonts:list[FontInfo]=[]
        for serif,bold in ((True,True),(True,False),(False,True),(False,False)):
            fonts.append(self._get_font(serif=serif,bold=bold,size=size))
        return fonts
    

        
    def parse(self,image:_AnyImage,text:str,*,full_image_size:tuple[int,int]|None=None)->FontInfo:
        """判断是否为衬线字体
        image: 文本对应的图片
        text: 文本
        full_image_size: 可以设置页面的图片大小，也就是image截图的来源，如果没有，就表示image不是截图
        """
        #计算颜色
        def get_colors(image:_Image,thresh:_Image,images:dict[str,_Image])->tuple[RGB,RGB]:
            #TODO 也可以对thresh进行裁剪
            images=dict(images)
            text_mask = thresh.copy()
            bg_mask = cv2.bitwise_not(text_mask)
            text_color = cv2.mean(image, mask=text_mask)[:3] # 取前三个通道 (B, G, R)
            bg_color = cv2.mean(image,mask=bg_mask)[:3]
            text_color =  tuple(int(v) for v in text_color)
            bg_color = tuple(int(v) for v in bg_color)
            b1,g1,r1 = text_color
            b2,g2,r2 = bg_color

            if debugger.allow('gui'):
                text_color_img = image.copy()
                text_color_img[:,:]=text_color
                bg_color_img = image.copy()
                bg_color_img[:,:]=bg_color
                images['image|text|bg']=np.hstack((image,text_color_img,bg_color_img))
                self._show_images(images)
            return ((r1,g1,b1),(r2,g2,b2))

        def is_bold(image:_Image,thresh:_Image,images:dict[str,_Image])->bool:
            images=dict(images)
            thresh = self._crop(thresh)
            images['crop thresh']=thresh
            method=1
            if method==1:
                # 统计白色像素总数
                total = cv2.countNonZero(thresh)
                # 腐蚀一次
                kernel = np.ones((3,3), np.uint8)
                eroded_image = cv2.erode(thresh, kernel, iterations=1)
                # 统计剩余像素
                remaining = cv2.countNonZero(eroded_image)
                images['eroded']=eroded_image

                bold = remaining/total>=0.65 if total>0 else False
                if debugger.allow('gui'):
                    debugger.print(f'percent={remaining/total:.3f},bold={bold}')
                    self._show_images(images)
                return bold
            else:
                dist_img = cv2.distanceTransform(thresh, cv2.DIST_L2, 5)
                images['crop|dist']=np.hstack((thresh,dist_img))
                # 3. 获取最大宽度（笔画最粗的地方，通常是笔画中心）
                # 注意：distanceTransform 得到的是半径，宽度 = 半径 * 2
                #或者dist_img>0
                stroke_width = np.mean(dist_img[dist_img > 1]) * 2
                h, w = thresh.shape[:2]
                ratio = stroke_width / h
                return ratio >= 0.14

        def draw_text(text:str,size:tuple[int,int],font:PIL.ImageFont.FreeTypeFont)->_Image:
            image = PIL.Image.new('L',size,0)
            draw = PIL.ImageDraw.Draw(image)
            #font.getbbox(text)
            #支持多行文本
            x0,y0,x1,y1=draw.textbbox((0,0),text,font)
            #print('==>',(x0,y0,x1,y1))
            #居中书写
            w,h=size
            x=(w-(x1-x0))//2
            y=(h-(y1-y0))//2
            draw.text((x,y),text,font=font,fill=255)
            return np.array(image)

        def mse_psnr(img1:_Image, img2:_Image)->float:
            """
            计算MSE和PSNR
            MSE越小越好，PSNR越大越好
            """
            # 确保图像类型和大小一致
            if img1.shape != img2.shape:
                img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
            
            # 转换为float32进行计算
            img1 = img1.astype(np.float32)
            img2 = img2.astype(np.float32)
            
            # 计算MSE
            mse = np.mean((img1 - img2) ** 2)
            
            # 计算PSNR
            if mse == 0:
                psnr = 100  # 完全相同
            else:
                psnr = 20 * np.log10(255.0 / np.sqrt(mse))
            
            return float(mse), float(psnr)
        
        def get_font(image:_Image,thresh:_Image,text:str,full_image_size:tuple[int,int]|None,bold:bool,images:dict[str,_Image])->FontInfo:
            template = self._crop(thresh)

            if full_image_size is not None:
                #表示完整的图片的大小，页面默认为4:3
                ratio=960/full_image_size[0]
                h,w=template.shape[0:2]
                #print('===>w,h',(w,h),h*ratio,'||',text)
                if h*ratio<=32:
                    #如果字体太小，就不判断了，总使用固定的黑体
                    return self._get_font(serif=False,bold=bold,size=64)
                
            #text = strs.NText.get(text,mode='b2q').text
            images=dict(images)
            #使用衬线字体书写一个
            #使用无衬线字体书写一个
            #黑底白字
            fontsize=64
            #TODO 或者4个字体都比较过？
            size=(fontsize*len(text)+fontsize,fontsize+fontsize)
            fonts:list[FontInfo]=self._get_fonts(fontsize)
            new_size = (template.shape[1],template.shape[0])
            text_images:list[_Image]=[template]
            for info in fonts:
                text_image = draw_text(text,size,info.font)
                text_image = self._crop(text_image)
                text_image=cv2.resize(text_image,new_size)
                text_images.append(text_image)

                #两个方法都可以，只是第一个方法需要引入一个库
                method=2
                if method==1:
                    from skimage.metrics import structural_similarity
                    score=structural_similarity(template,text_image,win_size=3)
                else:
                    _,score = mse_psnr(template,text_image)
                info.score=round(float(score),1)

            images['text images']=np.vstack(text_images)
            if debugger.allow('info'):
                with debugger.group('scores'):
                    for info in fonts:
                        debugger.console.print(info)
            
            
            fonts.sort(key=lambda info:info.score,reverse=True)
            if debugger.allow('gui'):
                self._show_images(images)
            
            #如果很接近的，在0.1范围内，如果为大字体，就是更加倾向粗体？
            if not fonts[0].bold and fonts[1].bold and fonts[0].score-fonts[1].score<=0.1:
                font=fonts[1]
            else: 
                font = fonts[0]
            #如果设置了这个，就必须同时更新font.font/font.face
            #if bold and not font.bold:
                #font=self._get_font(serif=font.serif,bold=bold,size=fontsize)
            return font
        
        debugger = self._debugger.bind()
        images:dict[str,Any]={}
        image = self.to_cv2_image(image)
        gray = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        images['original']=image
        images['gray']=gray

        #把浅色背景深色字，转换为黑底白字
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)


        #如果是深色背景，浅色字，会转换为白底黑字，再转换为黑底白字
        #特别的情况：深色背景，深色字，然后字体周围使用艺术效果，可以看得见字，转换后背景也是白色，字是白色，字周围为黑色
        #倒置被判断为深色背景，然后反转
        if self._is_dark_bg(thresh):
            thresh2=cv2.bitwise_not(thresh)
            images['gray|thresh|thresh2']=np.vstack((gray,thresh,thresh2))
            thresh = thresh2
        else:
            images['gray|thresh']=np.vstack((gray,thresh))
        bold = is_bold(image,thresh,images)
        font = get_font(image,thresh,text,full_image_size,bold,images)
        color,bg_color = get_colors(image,thresh,images)
        font.color = color
        font.bg_color = bg_color
        return font
        
    def get_colors(self,image:_AnyImage)->tuple[RGB,RGB]:
        """获得图片中文字的颜色和背景颜色

        image:bgr颜色的图片

        返回:[text_color,bg_color]
        """
        #img = cv2.imread(image_path)

        debugger = self._debugger.bind()
        images:dict[str,_Image]={}
        image = self.to_cv2_image(image)
        gray_image = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)

        images['original']=image
        images['gray']=gray_image
        #浅色背景+深色文字，转换为黑底白字
        _, thresh = cv2.threshold(gray_image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        #深色背景+浅色文字，转换为黑底白字
        if self._is_dark_bg(thresh):
            thresh=cv2.bitwise_not(thresh)
        images['binary']=thresh
        # 2. 计算 Mask 区域内的平均颜色
        # cv2.mean 可以接收 mask 参数，只计算 mask 中非零区域的颜色
        text_mask = thresh.copy()
        bg_mask = cv2.bitwise_not(text_mask)
        
        text_color = cv2.mean(image, mask=text_mask)[:3] # 取前三个通道 (B, G, R)
        bg_color = cv2.mean(image,mask=bg_mask)[:3]
        text_color =  tuple(int(v) for v in text_color)
        bg_color = tuple(int(v) for v in bg_color)

        if debugger.allow('gui'):
            text_color_img = image.copy()
            text_color_img[:,:]=text_color
            bg_color_img = image.copy()
            bg_color_img[:,:]=bg_color
            images['image|text|bg']=np.hstack((image,text_color_img,bg_color_img))
            self._show_images(images)
        b1,g1,r1 = text_color
        b2,g2,r2 = bg_color
        return ((r1,g1,b1),(r2,g2,b2))
    
    def is_bold(self,image:_AnyImage)->bool:
        """判断是否为粗体，可以使用单个字符，也可以使用字符串的图片"""
        debugger = self._debugger.bind()
        images:dict[str,_Image]={}
        image = self.to_cv2_image(image)
        #TODO 对图片进行一下缩放？
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        images['original']=image
        images['gray']=gray
        #浅色背景+深色文字，转换为黑底白字
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        if self._is_dark_bg(thresh):
            thresh = cv2.bitwise_not(thresh)
        images['binary']=thresh

        thresh = self._crop(thresh)

        images['crop binary']=thresh

        method=1
        if method==1:
            # 统计白色像素总数
            total = cv2.countNonZero(thresh)
            # 腐蚀一次
            kernel = np.ones((3,3), np.uint8)
            eroded_image = cv2.erode(thresh, kernel, iterations=1)
            # 统计剩余像素
            remaining = cv2.countNonZero(eroded_image)
            images['eroded']=eroded_image

            bold = remaining/total>=0.65
            if debugger.allow('gui'):
                debugger.print(f'percent={remaining/total:.3f},bold={bold}')
                self._show_images(images)
            return bold
        else:
            dist_img = cv2.distanceTransform(thresh, cv2.DIST_L2, 5)
            images['crop|dist']=np.hstack((thresh,dist_img))
            # 3. 获取最大宽度（笔画最粗的地方，通常是笔画中心）
            # 注意：distanceTransform 得到的是半径，宽度 = 半径 * 2
            #或者dist_img>0
            stroke_width = np.mean(dist_img[dist_img > 1]) * 2
            h, w = thresh.shape[:2]
            ratio = stroke_width / h
            return ratio >= 0.14