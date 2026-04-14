function mult_matrix(m1, m0) {
    let [a1, b1, c1, d1, e1, f1] = m1;
    let [a0, b0, c0, d0, e0, f0] = m0;

    return [
        a0 * a1 + c0 * b1,
        b0 * a1 + d0 * b1,
        a0 * c1 + c0 * d1,
        b0 * c1 + d0 * d1,
        a0 * e1 + c0 * f1 + e0,
        b0 * e1 + d0 * f1 + f0,
    ]
}

class M {
    constructor(a, b, c, d, e, f, g) {
        this.a = a
        this.b = b
        this.c = c
        this.d = d
        this.e = e
        this.f = f
    }
    prepend(m) {
        function round(a) {
            return Math.round(a * 1000) / 1000
        }
        let m2 = mult_matrix(m, [this.a, this.b, this.c, this.d, this.e, this.f])
        this.a = round(m2[0])
        this.b = round(m2[1])
        this.c = round(m2[2])
        this.d = round(m2[3])
        this.e = round(m2[4])
        this.f = round(m2[5])
        //[this.a, this.b, this.c, this.d, this.e, this.f] = m2
    }

    prerotate(angle) {
        //正值表示顺时针
        //[cos q,sin q,-sin q,cos q,0,0]
        //angle = angle % 360
        //angle = -angle
        //q = Math.radians(angle)
        //css的处理和pdf的处理刚好相反，不需要-angle
        //当angle=90，表示顺时针旋转90度，这里需要获得逆时针90度的值
        let q = angle * (Math.PI / 180)
        let a = Math.cos(q)
        let b = Math.sin(q)
        let m = [a, b, -b, a, 0, 0]
        this.prepend(m)
    }
    prescale(x, y) {
        this.prepend([x, 0, 0, y, 0, 0])
    }
}

function escapeHtml(s) {
    s = s || ''
    return s.replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
class PageHelper {
    constructor() {

    }
}

class App {
    constructor(outlineData, doc) {
        //这个需要reactive
        this.outlineData = outlineData
        //这个不需要reactive，所以可以为很复杂也不怎么影响性能
        this.doc = doc
        this.setup()
    }
    setup() {
        if (typeof Vue === 'undefined' || typeof ElementPlus === 'undefined') {
            alert('网络存在问题，不能够下载依赖的js/css文件');
            return
        }

        const params = new URLSearchParams(window.location.search);
        //默认为true
        const sidebarVisible = params.get('sidebarVisible') != 'false'
        //原文，可以为pdf/图片，docx等就不支持
        //默认为false，原文不可见
        const fileVisible = params.get('fileVisible') == 'true'
        const fileUrl = params.get('fileUrl')
        const fileType = params.get('fileType') ? params.get('fileType') : 'pdf'
        //http://39.104.186.99:8008/pdfjs/web/viewer.html
        //https://mozilla.github.io/pdf.js/web/viewer.html
        const pdfViewerUrl = params.get('pdfViewerUrl') ? params.get('pdfViewerUrl') : 'http://39.104.186.99:8008/pdfjs/web/viewer.html'
        const usePdfViewer = params.get('usePdfViewer')=='true'
        //lite or full
        const mode = params.get('mode') ? params.get('mode') : 'full'

        //如果在url中设置了文件可见，那么就先不显示并排图片了
        const imageVisible = fileVisible ? false : true

        //如果一开始
        let currentPageNumber = this.getPageNumberByHash()
        if (currentPageNumber == -1) {
            //否则就使用第一个
            currentPageNumber = this.doc.pages[0].number
        }

        const appThis = this

        this.app = Vue.createApp({
            //el:'#app',
            data: () => {
                return {
                    //full or lite
                    mode: mode,
                    filters: {
                        header: true,
                        footer: true,
                        footnote: true,
                        body: true,
                        other: true,
                        'read-order': true,
                        //可以添加更多,text,table,title
                    },
                    zoom: 1,
                    fontFamily: 'monospace',
                    fonts: [
                        'monospace',
                        'serif',
                        'sans-serif',
                        'Arial'
                    ],
                    outline: {
                        data: this.outlineData,
                        props: {
                            label: 'title',
                            children: 'children'
                        },
                        style: {
                            'min-height': '100%'
                        }
                    },

                    doc: {
                        currentPageNumber: currentPageNumber,
                        //true表示并排显示页面的结果+页面的图片
                        imageVisible: imageVisible,
                        //true表示为页面滚动中
                        scrolling: false,
                        //
                        pages: this.doc.pages,
                    },
                    sidebar: {
                        visible: sidebarVisible,
                        collapsible: true,
                        resizable: true,
                        size: 250,
                        min: 20,
                        max: 300
                    },
                    file: {
                        //控制原文的显示
                        visible: fileVisible,
                        url: fileUrl,
                        type: fileType,

                        //false表示使用浏览器自带的
                        usePdfViewer: usePdfViewer,
                        pdfViewerUrl: pdfViewerUrl,

                        //当原文为图片且很大，可以设置为auto，慢慢滚动细看
                        imageMode: 'contain',
                        imageModes: ['contain', 'auto']

                    }
                }
            },
            computed: {
                docStyle() {
                    const style = {
                        //表示并排显示每一页的结果和页面的图片
                        '--image-display': this.doc.imageVisible ? 'block' : 'none', '--font-family': this.fontFamily
                    }
                    for (const key in this.filters) {
                        style[`--page-${key}-display`] = this.filters[key] ? 'block' : 'none'
                    }
                    return style
                },
                pdfUrl() {
                    if (this.file.usePdfViewer) {
                        return `${this.file.pdfViewerUrl}?file=${encodeURIComponent(this.file.url)}#page=${this.doc.currentPageNumber}`
                    } else {
                        //目前page仅仅在刚加载的时候有效，后续更新无效，但是也不会重新载入页面
                        return `${this.file.url}#page=${this.doc.currentPageNumber}`
                    }
                }
            },
            watch: {
                'doc.currentPageNumber': function (newValue, oldValue) {
                    //在滚动的时候获得最新的页码，会更新，但是并不需要
                    const scroll = !this.doc.scrolling
                    this.doc.scrolling = false
                    this.showPageByNumber(newValue, scroll)

                },
                fontFamily() {
                    this.ensureFont(this.fontFamily)
                }
            },
            methods: {
                resetFilters() {
                    for (const key in this.filters) {
                        this.filters[key] = true
                    }
                },
                onNodeClick(data, node, comp) {
                    if (data.type == 'st.element') {
                        //highlightTag(data)
                        // onClickStrutTreeElement(data)
                    } else if (data.type == 'page.tag') {
                        //onClickPageTag(data)
                    } else if (data.type == 'pages' || data.type == 'pdf') {
                        //cancelHighlightTags()
                        //cancelHighlightForms()
                    } else if (data.type == 'form') {
                        //onClickForm(data)
                    } else if (data.type == 'xnode') {
                        //console.log('===>', data.page_id, data.el_id)
                        if (typeof data.el_id !== 'undefined') {
                            this.gotoElement(document.getElementById(data.el_id))
                        } else {
                            this.gotoElement(document.getElementById(data.page_id))
                            //document.getElementById(data.page_id).scrollIntoView()
                        }
                        if (typeof data.page_id !== 'undefined') {
                            //因为采用了延迟显示，要么如下：滚动到页面，让前面的所有页面先显示（渲染）
                            //才能够滚动到元素
                            //document.getElementById(data.page_id).scrollIntoView()

                            //要么就把前面的每一个页面都先显示了
                            //document.getElementById(data.page_id).style.display = 'block';




                        }

                    }
                },
                onChangePageNumber(n) {
                    //仅仅在选择改变的时候触发，改变v-model的值不会
                    //所以滚动到页面
                    this.showPageByNumber(n, true)
                },
                onChangeMode(mode) {
                    //console.log('mode',mode)
                    //现在使用变量即可了
                    function toggleClass(selector, className, a) {
                        const els = document.querySelectorAll(selector);
                        for (const el of els) {
                            el.classList.toggle(className, a)
                        }
                    }
                    if (mode == 'all') {
                        //显示全部
                        toggleClass('.body', 'hide-object', false)
                        toggleClass('.header', 'hide-object', false)
                        toggleClass('.footer', 'hide-object', false)
                        toggleClass('.footnote', 'hide-object', false)
                        toggleClass('.other', 'hide-object', false)
                    } else if (mode == 'body') {
                        //仅仅显示body
                        toggleClass('.body', 'hide-object', false)
                        toggleClass('.header', 'hide-object', true)
                        toggleClass('.footer', 'hide-object', true)
                        toggleClass('.footnote', 'hide-object', true)
                        toggleClass('.other', 'hide-object', true)
                    } else if (mode == 'hideBody') {
                        //隐藏body
                        toggleClass('.body', 'hide-object', true)
                    } else if (mode == 'hideHeader') {
                        toggleClass('.header', 'hide-object', true)
                    } else if (mode == 'hideFooter') {
                        toggleClass('.footer', 'hide-object', true)
                    } else if (mode == 'hideFootnote') {
                        toggleClass('.footnote', 'hide-object', true)
                    } else if (mode == 'hideOther') {
                        toggleClass('.other', 'hide-object', true)
                    }



                },
                onChangeZoom(zoom) {
                    //或者使用css var的方式，如下，只需要更新变了--xxx即可
                    //left:20%,width:30%,font-size:calc(var(--xxxx)*10px)
                    const els = document.querySelectorAll('.doc')
                    for (const el of els) {
                        el.style.zoom = zoom
                    }
                    //TODO 将来使用css变量的，可以如下，在页面显示的时候化，使用width:calc(var(--scale)*500px)
                    //const root = document.querySelector(':root')
                    //root.style.setProperty('--scale',String(zoom))
                },
                onChangeFont(font) {
                    //updateFont2(font)
                },
                onHelp(event) {
                    const html = `
                    <table class="help-table">
                    <colgroup><col style="width:80px;"/><col/></colgroup>
                    <tr><td>表格节点：</td><td>[page_number,block_index]</td></tr>
                    <tr><td>复制表格：</td><td>在页面上的表格双击</td></tr>
                    <tr><td>表格图片：</td><td>alt+表格单击</td></tr>
                    </table>
                    `;
                    this.$alert(html, '说明', {
                        showClose: false,
                        dangerouslyUseHTMLString: true,
                        confirmButtonText: '关闭'
                    });
                },
                onInfo(event) {
                    const pdfInfo = this.doc['pdf_info'] || {};

                    const html = `
                    <table class="info-table">
                    <colgroup><col style="width:80px;"/><col/></colgroup>
                    <tr><td>title:</td><td>${escapeHtml(pdfInfo.title)}</td></tr>
                    <tr><td>producer:</td><td>${escapeHtml(pdfInfo.producer)}</td></tr>
                    <tr><td>creator:</td><td>${escapeHtml(pdfInfo.creator)}</td></tr>
                    <tr><td>source:</td><td>${escapeHtml(pdfInfo.source)}</td></tr>
                    </table>
                    `;
                    this.$alert(html, 'INFO', {
                        showClose: false,
                        dangerouslyUseHTMLString: true,
                        confirmButtonText: '关闭'
                    });
                },
                onCommand(item) {
                    console.log(item)
                    alert(item)

                },
                onDragOver(event) {
                    console.log("onDragOver");
                },
                onDrop(event) {
                    //TODO 如果在本地打开doc.html
                    //然后拖动本地的pdf(blob:xxxx)，那么，pdfViewer是显示不了该文件的，因为跨域了
                    //可以先显示pdfViewer，然后拖动到pdfViewer中，就可以
                    event.preventDefault();
                    let dt = event.dataTransfer;
                    //dt.getData('text/html')
                    for (let file of dt.files) {
                        //console.log(file);
                        if (this.file.url && this.file.url.startsWith('blob:')) {
                            URL.revokeObjectURL(this.file.url)
                        }
                        this.file.url = URL.createObjectURL(file)
                        this.file.visible = true
                        if (file.type == 'application/pdf') {
                            this.file.type = 'pdf'
                        } else if (file.type && file.type.startsWith('image/')) {
                            this.file.type = 'image'
                        } else {
                            //不知道的？
                            this.file.type = 'pdf'
                        }

                        //如果显示了原文，默认就不显示并排图片了，除非再指定
                        //this.doc.imageVisible = false

                        break
                    }
                },
                togglePages() {
                    let value = document.body.style.getPropertyValue('--image-display');
                    //console.log(value)
                    if (!value) {
                        value = 'none'
                    } else {
                        value = ''
                    }
                    document.body.style.setProperty('--image-display', value);
                },
                updatePageNumberByScroll(event) {
                    //目前可见的页面表示已经允许渲染的，浏览器为了性能，可以多允许前后几页，也就是不一定在目前可见区域
                    //如果使用this.getVisiblePages2()，这个方法在zoom!=1的时候，就会出现不一致，因为使用的计算都是没有zoom的时候
                    const visiblePages = this.getVisiblePages2();
                    
                    for (const page of visiblePages) {
                        //仅仅显示页面
                        //page.el.style.display = 'block';
                    }


                    if (visiblePages.length > 0) {
                        //需要考虑缩小的时候，多页一起显示的问题
                        //如果显示最后一页，且完全显示区域，就使用最后一页的页码
                        //如果显示第一页，且完全显示区域，就使用第一页的页码
                        //如何判断为第一页还是最后一页
                        const n = visiblePages[0].number;
                        if (this.doc.currentPageNumber != n) {
                            //表示是因为滚动引起的页码改变
                            this.doc.scrolling = true
                            this.doc.currentPageNumber = n
                        } else {
                            this.doc.scrolling = false
                        }

                    }
                },
                getVisiblePages(){
                    const el = document.getElementById('doc-wrapper')
                    const visiblePages = [];
                    const pages = el.querySelectorAll('.page-wrapper');
                    for(let page of pages){
                        if(page.checkVisibility({contentVisibilityAuto:true})){
                            visiblePages.push({
                                el: page.querySelector('.page'),
                                wrapper: page,
                                number: parseInt(page.querySelector('.page').dataset.number),
                                percent: 1
                            })
                        }
                    }
                    return visiblePages
                },
                getVisiblePages2() {
                    
                    const el = document.getElementById('doc-wrapper')
                    function hasOverlap(a, b) {
                        //判断a和b是否有重叠（相交）
                        if (a[2] <= b[0] || a[0] >= b[2] || a[1] >= b[3] || a[3] <= b[1]) {
                            return false;
                        } else {
                            return true;
                        }
                    }
                    const visibleRect = [
                        el.scrollLeft + el.offsetLeft,
                        el.scrollTop + el.offsetTop,
                        el.scrollLeft + el.offsetLeft + el.clientWidth,
                        el.scrollTop + el.offsetTop + el.clientHeight
                    ];
                    //找到当前可见区域最大的页面，就认为是当前的页面

                    const visiblePages = [];
                    const pages = el.querySelectorAll('.page-wrapper');
                    const zoom = parseFloat(document.querySelector('.doc').style.zoom || '1')
                    for (let page of pages) {
                        //获得page在可见区域的坐标，原点为viewer的左上角
                        const rect = [
                            page.offsetLeft + page.clientLeft,
                            page.offsetTop + page.clientTop,
                            page.offsetLeft + page.clientLeft + page.clientWidth,
                            page.offsetTop + page.clientTop + page.clientHeight
                        ]
                        rect[0]*=zoom
                        rect[1]*=zoom
                        rect[2]*=zoom
                        rect[3]*=zoom
                        //console.log('=====>>>',zoom, rect, visibleRect, hasOverlap(rect, visibleRect))
                        if (hasOverlap(rect, visibleRect)) {
                            const area = (page.clientWidth * page.clientHeight)*zoom;
                            const x0 = Math.max(rect[0], visibleRect[0]);
                            const x1 = Math.min(rect[2], visibleRect[2]);
                            const y0 = Math.max(rect[1], visibleRect[1]);
                            const y1 = Math.min(rect[3], visibleRect[3]);
                            const overlapArea = (x1 - x0) * (y1 - y0);
                            //精确到.01即可
                            const percent = parseInt(overlapArea / area * 100);
                            visiblePages.push({
                                el: page.querySelector('.page'),
                                wrapper: page,
                                number: parseInt(page.querySelector('.page').dataset.number),
                                percent: percent
                            });
                        }
                    }

                    visiblePages.sort((a, b) => {
                        //放宽比较，如：0.8345 == 0.8365
                        const p1 = a.percent;
                        const p2 = b.percent;
                        if (p1 === p2) {
                            //如果面积相等，上一页优先，也就是同时显示n个页面，就仅仅显示最前面的页码
                            return a.number - b.number>0?1:-1;
                        } else {
                            //大的排前面
                            return p1-p2>0?-1:1;
                        }
                    });
                    //日志的时候需要复制一个，否则后面会添加新的页面到这个对象中
                    return visiblePages
                },

                gotoElement(el) {
                    //因为采用按需显示，就需要先设置display:block
                    this.showPage(el.closest('.page-wrapper'))
                    //el.scrollIntoView({ inline: 'nearest', block: 'start', behavior: 'instant' });
                    el.scrollIntoView(true)

                },
                setupObserver() {
                    //对于上千页的文档，可以按需显示
                    //但是生成的html需要做一些处理
                    //可以先设置
                    //<div class="page-placeholder" style=""><div class="page" style="display:none;"></div></div>
                    //<div class="image-placeholder" style=""><div class="image" style="display:none;"></div></div>
                    const options = {
                        //chrome>=81
                        //root: null,//document.body,
                        root: document.getElementById('doc-wrapper'),
                        rootMargin: '0px',
                        threshold: 0.1
                    };
                    const callback = (entries, observer) => {
                        entries.forEach(entry => {
                            //console.log('==>',entry.isIntersecting)
                            if (entry.isIntersecting) {
                                this.showPage(entry.target)
                            }
                            // Each entry describes an intersection change for one observed
                            // target element:
                            //   entry.boundingClientRect
                            //   entry.intersectionRatio
                            //   entry.intersectionRect
                            //   entry.isIntersecting
                            //   entry.rootBounds
                            //   entry.target
                            //   entry.time
                        });
                    };

                    const observer = new IntersectionObserver(callback, options);
                    for (let page of document.querySelectorAll('.page-wrapper')) {
                        observer.observe(page);
                    }
                },
                setupContentVisibilityAutoStateChange(){
                    document.querySelectorAll('.page-wrapper').forEach((el)=>{
                        el.addEventListener('contentvisibilityautostatechange',(event)=>{
                            //
                            if(!event.skipped){
                                //console.log(event.target.dataset['number'])
                                this.showPage(event.target)
                            }
                        })
                    })
                },
                showPage(wrapper) {
                    //el is 'page-wrapper'
                    //现在使用了content-visiblity，浏览器自动控制内容在需要的时候渲染了，可以不需要在这里控制display
                    const el = wrapper.querySelector('.page')
                    el.style.display = 'block';
                    const el2 = wrapper.querySelector('.image')
                    el2.style.display = 'block';
                    this.ensureFont(this.fontFamily)
                    this.ensureFormulas()
                },
                showPageByNumber(number, scroll = false) {
                    //<div class='page-wrapper' id='page_1' data-number='1'></div>
                    //因为id可以这么构造，或者
                    //const el = document.getElementById('.doc').querySelector(`.page-wrapper[data-number="${number}"]`)
                    const el = document.getElementById(`page_${number}`)
                    //this.showPage(el)
                    if (scroll) {
                        //表示需要滚动到该页面
                        el.scrollIntoView(true)
                    }
                },
                ensureFont(family) {
                    //确保已经对该字体进行了计算，显示更加友好
                    //因为有些页面是按需显示的，所以字符串的计算可以如下计算
                    const tpl = document.createElement('span')
                    tpl.classList.add('span')
                    tpl.style.visibility = 'hidden'
                    document.body.append(tpl)
                    //一下子更新全部页面会太慢了
                    //可以提供一个元素，如：.page的，这样按需更新就可以
                    //当显示页面的时候，再计算字体的，如果已经计算过了，就不需要再调整
                    //const root = document.getElementById('doc')
                    //this.getVisiblePages()
                    for (const page of this.getVisiblePages()) {
                        //root可以为<div id='doc'> or <div id='page_1'>
                        const pageEl = page.el
                        if (pageEl.dataset['fontFamily'] === family) {
                            //表示已经更新过了
                            continue
                        }

                        //console.log('=======>>ensure font', pageEl)
                        for (const el of pageEl.querySelectorAll('.span')) {
                            
                            if(el.style.fontFamily && el.style.fontFamily.includes('Wingdings')){
                                //不用做任何调整了
                                
                                continue
                            }
                            if (family) {
                                el.style.fontFamily = family
                            }
                            tpl.style.fontFamily = el.style.fontFamily
                            tpl.style.fontSize = el.style.fontSize
                            tpl.style.fontWeight = el.style.fontWeight
                            tpl.style.fontStyle = el.style.fontStyle
                            tpl.style.writingMode = el.style.writingMode
                            tpl.textContent = el.textContent

                            //通常为1
                            //const scale = parseFloat(el.dataset.scale||1)
                            const scale = parseFloat(pageEl.dataset.htmlScale || 1)
                            const width = parseFloat(el.dataset.width || 0) * scale;
                            const height = parseFloat(el.dataset.height || 0) * scale;
                            const rotate = parseInt(el.dataset.rotate || 0);
                            const vertical = el.dataset.vertical == 'true'
                            if (rotate == 0) {
                                if (vertical) {
                                    //console.log('=====>>', tpl.style.fontSize,tpl.style.fontFamily, tpl.textContent, tpl.offsetWidth, tpl.offsetHeight)
                                    const rate = Math.round(height / tpl.offsetHeight * 1000) / 1000;
                                    el.style.transformOrigin = '0% 0%';//left top
                                    el.style.transform = `scaleY(${rate})`;
                                } else {
                                    const rate = Math.round(width / tpl.offsetWidth * 1000) / 1000;
                                    el.style.transformOrigin = '0% 0%';//left top
                                    el.style.transform = `scaleX(${rate})`;
                                }
                            } else {
                                if (vertical) {
                                    const rate = Math.round(height / tpl.offsetHeight * 1000) / 1000;
                                    //el.style.transformOrigin = '0% 0%';
                                    //el.style.transform = `scaleY(${rate}) ${el.style.transform}`;
                                    el.style.transformOrigin = '0% 0%';
                                    //el.style.transform = `scaleX(${rate}) ${el.style.transform}`;
                                    const m = new M(1, 0, 0, 1, 0, 0)
                                    m.prerotate(rotate)
                                    m.prescale(1, rate)
                                    el.style.transform = `matrix(${m.a},${m.b},${m.c},${m.d},${m.e},${m.f})`
                                } else {
                                    const rate = Math.round(width / tpl.offsetWidth * 1000) / 1000;
                                    el.style.transformOrigin = '0% 0%';
                                    //el.style.transform = `scaleX(${rate}) ${el.style.transform}`;
                                    const m = new M(1, 0, 0, 1, 0, 0)
                                    m.prerotate(rotate)
                                    m.prescale(rate, 1)
                                    el.style.transform = `matrix(${m.a},${m.b},${m.c},${m.d},${m.e},${m.f})`
                                }
                            }
                        }

                        //标记使用了该字体
                        pageEl.dataset['fontFamily'] = family
                    }

                    tpl.remove()

                },
                ensureFormulas() {
                    //MathJax会根据配置（或者已经配置好的库），自动转换为svg，如：
                    //tex-svg.js => 当检查到tex，就自动转换为svg
                    //tex-mml-svg.js => 当检查到tex，mathml，就自动转换为svg
                    //所以：如果加载了tex-mml-svg.js，下面的代码获得的html为mathml，然后会自动转换为svg
                    const useSvg = true
                    for (const page of this.getVisiblePages()) {
                        const el = page.el
                        for (const formulaEl of el.querySelectorAll('div[data-type="formula"]')) {
                            //如果还没有渲染
                            const formula = appThis.doc.objects[formulaEl.id]
                            if (formula.latex && formulaEl.dataset['done'] != 'true') {

                                formulaEl.dataset['done'] = 'true'
                                //tex2svg => 返回的是元素
                                if (useSvg) {
                                    MathJax.tex2svgPromise(formula.latex,{display:true}).then((svgEl)=>{
                                        formulaEl.innerHTML = ''
                                        formulaEl.appendChild(svgEl)
                                    },(err)=>{
                                        console.log(err)
                                    })
                                    //const svgEl = MathJax.tex2svg(formula.latex, { display: true })
                                    //formulaEl.innerHTML = ''
                                    //formulaEl.appendChild(svgEl)
                                } else {
                                    MathJax.tex2mmlPromise(formula.latex, {
                                        //false表示为inline
                                        display: true
                                    }).then((html)=>{
                                        formulaEl.innerHTML=html
                                    },(err)=>{console.log(err)})
                                    //const html = MathJax.tex2mml(formula.latex, {display: true})
                                    //formulaEl.innerHTML = html
                                }
                            }

                        }
                    }
                },

                setupXHover() {
                    //设置跨页合并的对象在hover的时候显示相同的效果
                    function onEnter(event) {
                        //现在对应节点的都有一个xid，如果需要跨页高亮的，还需要
                        const xid = event.currentTarget.dataset.xid
                        //console.log('enter', xid)

                        for (const el of document.getElementById('doc').querySelectorAll(`[data-xid="${xid}"]`)) {
                            el.classList.toggle('x-hover', true)
                        }
                    }
                    function onLeave(event) {
                        const xid = event.currentTarget.dataset.xid
                        //console.log('leave', xid)
                        for (const el of document.getElementById('doc').querySelectorAll(`[data-xid="${xid}"]`)) {
                            el.classList.toggle('x-hover', false)
                        }
                    }
                    function setup(method = 1) {
                        const docEl = document.getElementById('doc')
                        const els = docEl.querySelectorAll('[data-xid][data-merged-index]')
                        //为了提高性能，在这里做一个缓存
                        const groups = {}
                        for (const el of els) {
                            if (method == 1) {
                                //不使用缓存
                                el.addEventListener('mouseenter', onEnter)
                                el.addEventListener('mouseleave', onLeave)
                            } else {
                                //在当前缓存了对象的集合，性能更好一点？
                                const xid = el.dataset.xid
                                let group = groups[xid]
                                if (!group) {
                                    group = []
                                    groups[xid] = group
                                }
                                group.push(el)
                                el.addEventListener('mouseenter', (event) => {
                                    //console.log('enter xid=', xid)
                                    for (const el2 of groups[xid]) {
                                        el2.classList.toggle('x-hover', true)
                                    }

                                })
                                el.addEventListener('mouseleave', (event) => {
                                    //console.log('leave xid=', xid)
                                    for (const el2 of groups[xid]) {
                                        el2.classList.toggle('x-hover', false)
                                    }
                                })
                            }


                        }
                    }

                    setup()
                },

                setupEvents() {
                    const pressedKeys = {}
                    //使用原始的doc对象
                    const doc = appThis.doc
                    const app = this

                    function onKeyDown(event) {

                        if (event.repeat) {
                            //表示一直按着不放
                            return
                        }
                        //console.log('keydown', event.keyCode, event.repeat)
                        pressedKeys[event.keyCode] = true;
                    }
                    function onKeyUp(event) {
                        //console.log(event);
                        //如果在其他窗口释放，就不能够获得该事件了
                        //忽略大小写的
                        //console.log('keyup', event.keyCode)
                        pressedKeys[event.keyCode] = false;
                        //console.log(pressedKeys)

                    }
                    //支持文本，表格的跨页复制
                    async function onDblClick(event) {
                        //表格内也可以包含公式，所以，先判断公式
                        let el = event.target.closest('.formula')
                        if (el) {
                            copyFormula(el)
                            return
                        }


                        el = event.target.closest('.table')
                        if (el) {
                            console.log('table', el, el.dataset.xid)
                            const mergedIndex = el.dataset.mergedIndex
                            if (event.altKey && (mergedIndex!==null && mergedIndex!==undefined && mergedIndex!=='')) {
                                //表示同时按下了alt，如果是跨页表格的，复制的是跨页表格
                                console.log('copy xtable',el.dataset.xid)
                                await copyTable(doc.xobjects[el.dataset.xid])
                            } else {
                                //复制页面表格
                                console.log('copy table', el.id)
                                //console.log(doc.objects)
                                await copyTable(doc.objects[el.id])
                            }
                        }





                    }
                    function onClick2(event) {
                        //点击且同时按下某个键，可以显示截图
                        //如果是跨页表格
                        //如果是公式，打开截图？
                        const el = event.target.closest('.formula')
                        if (el) {
                            const dialog = document.getElementById('figure-dialog')
                            const formula = doc.objects[el.id]
                            const html = `<img src="images/${formula.filename}">`
                            dialog.innerHTML = html
                            dialog.showModal()
                        } else {
                            //
                        }

                    }

                    function onClick(event) {
                        const autoScroll=false
                        const tree = app.$refs.tree
                        const el = event.target.closest('[data-xid]')
                        //console.log('xel', el)
                        if (el) {
                            let index = el.dataset.mergedIndex
                            let xid = el.dataset.xid
                            //如果没有的，总是可以设置为0，然后判断是否有更细的节点
                            if (index === null || index == undefined || index === '') {
                                index = 0
                            }

                            const xid2 = `${xid}_${index}`
                            const node = tree.getNode(xid2)
                            console.log(xid2,node)
                            if (node) {
                                //表示有更细的节点
                                xid = node.data.id
                            } else {
                                //
                            }
                            tree.setCurrentKey(xid, true)
                            if (autoScroll) {
                                const treeEl = tree.$el
                                const nodeEl = treeEl.querySelector(`[data-key="${xid}"]`)
                                if (nodeEl) {
                                    //前面选择了新的节点（为延迟更新dom），但是dom还没有发生真正的改变，或者节点元素还没有渲染
                                    //执行下面不能够滚动，需要再次点击（也就是dom更新了后），才能够真正滚动
                                    nodeEl.scrollIntoView(true)
                                }

                            }

                        }
                    }

                    async function copyFormula(el) {
                        //el=<div id='xxx'>
                        const formula = doc.objects[el.id]
                        await navigator.clipboard.writeText(formula.latex)
                    }

                    function copyTable2(table) {
                        //table is Element
                        const selection = document.getSelection();
                        const range = document.createRange();
                        range.selectNode(table);
                        selection.removeAllRanges();
                        selection.addRange(range);
                        document.execCommand('copy');

                    }
                    async function copyTable(table) {
                        //const html='<table><tr><td>1</td><td>2</td></tr><tr><td colspan="2">3</td></tr></table>'
                        const html = table2html(table)
                        console.log(html)
                        const blobHtml = new Blob([html], { type: "text/html" });
                        const data = [
                            new ClipboardItem({
                                "text/html": blobHtml
                            }),
                        ];
                        await navigator.clipboard.write(data)
                        console.log('==========')
                    }

                    function table2html(table) {
                        //跨页表格
                        //table is {}
                        const rows = [];
                        for (const cell of table.cells) {
                            //创建一个新的表格
                            let row = null;
                            if (cell.row_index < rows.length) {
                                row = rows[cell.row_index];
                            } else {
                                row = [];
                                rows.push(row);
                            }
                            row.push(cell);
                        }

                        const trs = []
                        for (const row of rows) {
                            const tr = ['<tr>']
                            for (const cell of row) {
                                const td = ['<td']
                                if (cell.row_span != 1) {
                                    td.push(` rowspan="${cell.row_span}"`)
                                }
                                if (cell.col_span != 1) {
                                    td.push(` colspan="${cell.col_span}"`)
                                }

                                td.push('>')
                                for (const obj of cell.objects) {
                                    if (obj.type == 'text') {
                                        td.push(escapeHtml(obj.text))
                                    } else if (obj.type == 'figure') {
                                        td.push(`<img src="${obj.filename}">`)
                                    } else if (obj.type == 'formula') {
                                        //latex??
                                        td.push(`<img src="${obj.filename}">`)
                                    }
                                }
                                td.push('</td>')
                                tr.push(td.join(''));
                            }
                            tr.push('</tr>')
                            trs.push(tr)
                        }
                        const buf = ['<table>']
                        for (const tr of trs) {
                            buf.push(tr.join(''))
                        }
                        buf.push('</table>')
                        return buf.join('')
                    }

                    const docEl = document.getElementById('doc')
                    docEl.addEventListener('dblclick', onDblClick)
                    docEl.addEventListener('click', onClick)


                    document.addEventListener('keydown', onKeyDown)
                    document.addEventListener('keyup', onKeyUp)
                }
            },

            mounted() {
                window.addEventListener("hashchange", () => {
                    const n = appThis.getPageNumberByHash()
                    if (n != -1) {
                        this.doc.currentPageNumber = n
                    }
                });

                //没有必要了
                //this.setupObserver()

                this.setupContentVisibilityAutoStateChange()

                document.getElementById('app').style.display = 'flex'

                this.showPageByNumber(this.doc.currentPageNumber, true)

                //如果需要显示跨页的效果
                this.setupXHover()

                this.setupEvents()




            }

        });

        this.app.use(ElementPlus);
        this.app.mount('#app');
    }

    getPageNumberByHash() {
        //可以在hash参数中指定页码
        //#page=1
        if (window.location.hash && window.location.hash.startsWith('#')) {
            const hashParams = new URLSearchParams(window.location.hash.substring(1))
            if (hashParams.get('page')) {
                //如果是在这里改变，不会发出change事件
                const number = parseInt(hashParams.get('page'))
                for (const page of this.doc.pages) {
                    if (page.number === number) {
                        return number
                    }
                }
            }
        }
        //表示没有或者设置的溢出了范围
        return -1
    }

    setupTableEvents() {
        //处理表格的操作，不需要改变对象的状态，
        //弹出窗口显示表格的截图
        //支持跨页/跨栏表格合并后的截图
        let pressedKeys = {}
        function onKeyDown(event) {

            if (event.ctrlKey && event.keyCode == 65) {
                //ctrl+'a'
                if (root.pdf.viewer == 'mozilla') {
                    root.pdf.visible = !root.pdf.visible
                } else {
                    root.pdf.visible = true
                    root.pdf.viewer = 'mozilla'
                }
            } else if (event.ctrlKey && event.keyCode == 90) {
                //ctrl+'z' 
                if (root.pdf.viewer == 'native') {
                    root.pdf.visible = !root.pdf.visible
                } else {
                    root.pdf.visible = true
                    root.pdf.viewer = 'native'
                }

            } else if (event.ctrlKey && event.keyCode == 81) {
                //ctrl+'q' 切换sidebar
                root.sidebar.visible = !root.sidebar.visible
            } else {
                pressedKeys[event.keyCode] = true;
            }
        }
        function onKeyUp(event) {
            //console.log(event);
            //如果在其他窗口释放，就不能够获得该事件了
            //忽略大小写的
            pressedKeys[event.keyCode] = false;
            //console.log(pressedKeys)

        }
        function onClick(event) {
            const el = event.target.closest('.table');
            if (el) {
                //console.log(pressedKeys)
                //81 => q or Q
                if (pressedKeys[81] && el.dataset.screenshotFilename) {
                    window.open(`images/${el.dataset.screenshotFilename}`, '_blank')
                } else if (pressedKeys[65] && el.dataset.masterScreenshotFilename) {
                    //65 => a or A
                    window.open(`images/${el.dataset.masterScreenshotFilename}`, '_blank')
                } else {
                }

                pressedKeys = {}
            }
        }
    }

    updateFont2() {
        //如果需要支持垂直书写
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        for (const el of document.querySelectorAll('.span')) {
            const family = el.style.fontFamily
            //需要在这里计算
            const text = el.textContent;
            //const width = parseInt(el.dataset.width);
            const width = parseInt(el.dataset.width);
            const rotate = parseInt(el.dataset.rotate || 0);
            const vertical = el.dataset.vertical == 'true'
            ctx.font = `${el.style.fontSize} ${family}`;

            if (vertical) {
                canvas.style.writingMode = 'vertical-rl'
            }

            //如果是垂直书写，获得高？
            const w1 = ctx.measureText(text).width;

            //el.style.fontFamily = family;
            //console.log(`${el.style.fontSize} ${family},${w1},${width}`);
            if (w1 > 0 && width > 0) {

                //如果有旋转的，就不需要0% 0%
                if (rotate != 0) {
                    el.style.transformOrigin = '';
                    el.style.transform = `scale(${width / w1}) rotate(${rotate}deg)`;
                } else {
                    //如果有旋转，也需要设置transform和transform-origin
                    //覆盖或者冲突？
                    if (vertical) {
                        el.style.transformOrigin = '0% 0%';
                        el.style.transform = ` scaleY(${width / w1})`;
                    } else {
                        el.style.transformOrigin = '0% 0%';
                        el.style.transform = ` scaleX(${width / w1})`;
                    }

                }


            }

        }
    }


    doCopyTable(event) {
        const page = event.target.closest('.page');
        if (page) {
            console.log(page)
            page.querySelector('.page-helper').classList.toggle('page-helper-hide')
        }
        const el = event.target.closest('.table');
        if (el) {
            this.copyTable(el)
        }
    }
    copyTable(table) {
        //table is Element
        const selection = document.getSelection();
        const range = document.createRange();
        range.selectNode(table);
        selection.removeAllRanges();
        selection.addRange(range);
        document.execCommand('copy');

    }

    copyTables(table) {
        //跨页表格
        //table is {}
        const rows = [];
        for (const cell of table.cells) {
            //创建一个新的表格
            let row = null;
            if (cell.row_index < rows.length) {
                row = rows[cell.row_index];
            } else {
                row = [];
                rows.push(row);
            }
            row.push(cell);
        }
        const tableEl = document.createElement("table");
        for (const row of rows) {
            const tr = document.createElement("tr");
            for (const c of row) {
                const td = document.createElement("td");
                td.setAttribute("rowspan", c.row_span);
                td.setAttribute("colspan", c.col_span);

                //直接设置记得使用大小写
                //td.rowSpan = c.row_span
                //td.colSpan = c.col_span
                td.textContent = c.text;
                //TODO 支持图片
                if (c.figures) {
                    for (const f of c.figures) {
                        //<img src="images/${}"/>
                        const imgEl = document.createElement('img');
                        imgEl.src = `images/${f.filename}`;
                        td.append(imgEl);
                    }
                }
                tr.append(td);
            }
            tableEl.append(tr);
        }

        //不能够设置为style.display='none';
        //tableEl.style.visibility='hidden';
        tableEl.classList.add("table", "table-ybk");

        //document.body.append(tableEl);
        //先清除之前的
        const debug = false;
        document.getElementById("copy-layer").innerHTML = "";
        document.getElementById("copy-layer").append(tableEl);
        this.copyTable(tableEl);
        //可以删除，debug的可以保留，因为下一次会清除，innerHTML=''
        if (!debug) {
            tableEl.parentElement.removeChild(tableEl);
        }
    }

}

