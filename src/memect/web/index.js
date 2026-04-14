

const { markRaw } = Vue

const utils = {
    async sleep(n) {
        await new Promise((resolve, reject) => {
            setTimeout(resolve, n)
        })
    },
    getExt(name) {
        const i = name.lastIndexOf('.')
        if (i >= 0) {
            return name.substring(i + 1).toLowerCase()
        } else {
            return ''
        }
    },
    isPDF(ext) {
        return ['pdf'].includes(ext)
    },
    isImage(ext) {
        return ['png', 'webp', 'gif', 'bmp', 'jpg', 'jpeg'].includes(ext)
    },
    isText(ext) {
        return ['txt', 'html', 'css', 'js', 'json', 'yaml', 'yml'].includes(ext)
    },
    isDocx(ext) {
        return ['doc', 'docx'].includes(ext)
    },

    async rotateImage(blob, rotate) {
        if (rotate == 0 || rotate == 360) {
            return blob
        }
        //blob可以为Blob，Image等，具体看看api
        const image = await window.createImageBitmap(blob)
        const canvas = document.createElement('canvas')
        if (rotate == 90 || rotate == 270) {
            canvas.width = image.height
            canvas.height = image.width
        } else {
            canvas.width = image.width
            canvas.height = image.height
        }

        //document.body.appendChild(canvas)
        const ctx = canvas.getContext('2d')
        //
        //ctx.save()
        //ctx.clearRect(0,0,canvas.width,canvas.height);

        ctx.translate(canvas.width / 2, canvas.height / 2)
        ctx.rotate(rotate * Math.PI / 180);
        ctx.drawImage(image, -image.width / 2, -image.height / 2)
        // ctx.restore()
        //console.log(image,canvas,ctx)
        return await new Promise((resolve, reject) => {
            canvas.toBlob(resolve, 'image/png')
        })


    },
    setCssValue(el, name, value) {
        //比如，定义在.page下的var，就可以这样设置了
        el.style.setProperty(name, value)
    },
    isEmpty(obj) {
        for (var prop in obj) {
            if (Object.prototype.hasOwnProperty.call(obj, prop)) {
                return false;
            }
        }

        return true
    },
    escapeHtml(s) {
        return s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
    },

    taskSeq: 1,
    newTask() {
        return {
            id: this.taskSeq++,
            //uploading,executing,downloading,success,error
            status: '',
            running: false,
            upload: { total: 0, loaded: 0, elapsed: 0 },
            execute: { elapsed: 0 },
            download: { total: 0, loaded: 0, elapsed: 0 },
            error: null,
            data: null,
            //表示执行该任务的时候的文件，因为显示的时候需要一致
            file: null
        }
    }
}

class AbstractFile {
    constructor() {
        this.urls = new Map()
    }
    async load(file) {

    }
    async getUrl(name, { rotate = 0, scale = 1 }) {

    }
    async destroy() {
        this.revokeUrls()
    }
    revokeUrls() {
        this.urls.forEach((url, name) => {
            console.log('revoke url', name, url)
            URL.revokeObjectURL(url)
        })
        this.urls.clear()
    }
}
class PDF extends AbstractFile {

    async load(file) {
        const a = await file.arrayBuffer()
        //'' or URL or ArrayBuffer
        //{url:''} or {data:array}
        this.loadingTask = pdfjsLib.getDocument(a)
        this.doc = await this.loadingTask.promise

    }
    async destroy() {
        super.destroy()
        if (this.loadingTask) {
            await this.loadingTask.destroy()
        }
    }
    async getUrl(name, { scale = 1, rotate = 0 }) {
        //1表示第一页
        let num
        if (typeof name == 'string') {
            num = parseInt(name)
        } else {
            num = name
        }
        //TODO 现在简化，对于同一个页码，获得url的参数必须一致，这里不检查了
        let url = this.urls.get(name)
        if (url) {
            return url
        }
        const canvas = document.createElement('canvas')
        const ctx = canvas.getContext('2d')

        const page = await this.doc.getPage(num)
        //设置的rotate为相对原文的，所以需要加上原文自身的rotate
        const viewport = page.getViewport({ scale: scale, rotation: (rotate + page.rotate) % 360 })
        canvas.height = viewport.height
        canvas.width = viewport.width

        // Render PDF page into canvas context
        const renderContext = {
            canvasContext: ctx,
            viewport: viewport
        }
        await page.render(renderContext).promise
        const blob = await new Promise((resolve, reject) => {
            canvas.toBlob(resolve, 'image/png')
        })
        if (blob == null) {
            throw new Error('无法获得blob')
        }
        url = URL.createObjectURL(blob)
        console.log('pdf create url', name, url)
        this.urls.set(name, url)
        return url
    }
}
class ZIP extends AbstractFile {
    async load(file) {
        this.zf = new window.JSZip()
        await this.zf.loadAsync(file)
    }
    async getUrl(name, { rotate = 0 }) {
        //对于同一个name，认为参数都是一样的
        let url = this.urls.get(name)
        if (url) {
            return url
        }
        //获得文件的url
        const entry = this.zf.file(name)
        //使用blob url或者data url
        const blob = await entry.async('blob')
        if (rotate != 0) {
            blob = await utils.rotateImage(blob, rotate)
        }
        url = URL.createObjectURL(blob)
        console.log('zip create url', name, url)
        this.urls.set(name, url)
        return url
    }
}
class NativeFile extends AbstractFile {

    async load(file) {
        this.file = file
    }

    async getUrl(name, { rotate = 0 }) {
        let url = this.urls.get(name)
        if (url) {
            return url
        }
        let blob
        if (rotate != 0) {
            blob = await utils.rotateImage(this.file, rotate)
        } else {
            blob = this.file
        }
        url = URL.createObjectURL(blob)
        console.log('native create url', name, url)
        this.urls.set(name, url)
        return url
    }
}
class App {
    constructor() {

    }
    async setup() {
        const viewers = [
            { label: '首页', name: 'index' },
            { label: '预览', name: 'preview' },
            { label: '系统', name: 'system' }
        ]
        const pdfViewers = ['native', 'pdfjs']

        const apis = await this.getApis()

        const state = Vue.reactive({
            api: apis[0],
            apis: apis,
            viewer: viewers[0],
            viewers: viewers,
            pdfViewer: pdfViewers[0],
            pdfViewers: pdfViewers
        })

        //console.log(state)

        //可以使用setup，或者使用传统的选项方式
        const app = Vue.createApp({
            inject: ['state'],
            components: {
                'x-sidebar': XSidebar,
                'x-index': XIndex,
                'x-preview': XPreview,
                'x-system': XSystem,
                'x-params': XParams
            },
            data() {
                return {

                }
            },
            provide() {
                return {

                }
            },
            watch: {

            },
            computed: {
                api() {
                    return this.state.api
                },
                viewer() {
                    return this.state.viewer
                }
            },
            methods: {

            },

        })

        app.provide('state', state)
        //全局使用
        app.use(ElementPlus)
        //app.use(naive)
        app.mount('#app')
    }

    async initMonaco(apis) {
        //如果是使用loader的方式
        if (true) {
            //如果是使用本地的
            //require.config({ paths: { 'vs': './node_modules/monaco-editor/min/vs' } });
            //https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs/
            require.config({ paths: { 'vs': 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs' } });
            await new Promise((resolve, reject) => {
                require(['vs/editor/editor.main'], resolve, reject)
            })
        }

        const schemas = []
        for (const api of apis) {
            //如果想避免需要设置"$schema"，可以使用文件名匹配
            const modelUri = monaco.Uri.parse(`api://${api.model.url}/params.json`)
            schemas.push({
                //使用上面的uri.toString()
                fileMatch: [modelUri.toString()],
                schema: api.model.schema,
                uri: `http://memect${api.model.url}/params.schema.json`
            })

            api.params['monaco'] = markRaw({
                model: null,
                uri: modelUri
            })

        }
        //console.log(schemas)

        monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
            allowComments: true,
            //允许带注释，但是无法设置某个editor，必须全局设置
            comments: 'ignore',
            schemas: schemas
        })
    }

    async getApis() {
        const models = await this.getApiModels()
        const apis = []
        let apiSeq = 1
        for (let model of models) {
            const api = {}
            api.id = apiSeq++
            api.model = markRaw(model)
            api.params = {
                'use_form': false,
                'async': false,
                'timeout': null,
                'task_id': null,
                //不需要关注其中的变化
                'data': markRaw({}),
                //根据schema生成默认的？或者后台返回默认的，这样更加一致？
                'defaults': markRaw({}),
                //编辑窗口可见
                'visible': false
            }

            api.task = utils.newTask()
            //本地选择的文件
            api.file = null
            //表示当前api为禁用，如：正在执行
            api.disabled = false

            //文件选择的accept的值
            const exts = []
            for (const t of model.file.types) {
                for (const ext of t.exts) {
                    exts.push(`.${ext}`)
                }
            }
            api.accept = exts
            apis.push(api)
        }
        //参数编辑使用到，初始化
        await this.initMonaco(apis)
        return apis
    }
    async getApiModels() {
        const res = await fetch('./apis', {})
        if (res.status == 200) {
            return await res.json()
        } else {
            //console.log(res.status)
            alert(`获得apis失败，status=${res.status}`)
        }
    }
}

const XApi = {
    template: '#x-api',
    inject: ['state'],
    props: [],
    data() {
        return {}
    },
    watch: {
        ['api.file']() {
            //如果重新选择了文件，就创建一个新的任务
            this.api.task = utils.newTask()
        }
    },
    computed: {
        api() {
            return this.state.api
        }
    },
    methods: {
        onDragOver(event) {
        },
        async onDropFile(event) {
            //console.log("ondrop");
            let dt = event.dataTransfer;
            await this.setFiles(dt.files)
        },
        onClickFile(event) {
            this.$refs.file.click()
        },
        async onSelectFile(event) {
            //console.log(event)
            const el = event.target
            await this.setFiles(el.files)
        },
        async setFiles(files) {
            if (files.length != 1) {
                this.$alert(`只允许选择一个文件，现在选择了:${files.length}`)
                return
            }
            const file = files[0]
            //目的是允许再次选择同一个文件
            this.$refs.file.value = ''
            const ext = utils.getExt(file.name)
            let ok = false
            for (const type of this.api.model.file.types) {
                if (type.exts.includes(ext)) {
                    if (file.size > type.max_length) {
                        this.$alert(`文件太大，超过了限制:${file.size}/${type.max_length}`)
                        return
                    }
                    ok = true
                    break
                }
            }


            if (!ok) {
                this.$alert(`不支持的文件类型:${ext}`)
                return
            }

            this.api.file = file
        },

        formatSize(size) {
            const units = [[1024 * 1024 * 1024, 'GB'], [1024 * 1024, 'MB'], [1024, 'KB']]
            for (const unit of units) {
                if (size >= unit[0]) {
                    const n = (size / unit[0]).toFixed(2)
                    return `${n}${unit[1]}`
                }
            }
            return `${size}B`
        }
    }
}

const XTask = {
    template: '#x-task',
    inject: ['state'],
    data() {
        return {}
    },
    computed: {
        api() {
            return this.state.api
        },
        task() {
            return this.state.api.task
        },
        mayExecute() {
            //还需要检查参数正确
            return !!this.state.api.file
        }
    },
    methods: {
        formatElapsed(t) {
            return parseInt(t / 1000 * 100) / 100
        },
        async toggle() {
            if (this.api.task.running) {
                await this.cancel()
            } else {
                await this.execute()
            }
        },
        async execute() {
            console.log('start api')
            this.api.disabled = true
            this.api.params.visible = false
            this.api.task = utils.newTask()

            const task = this.api.task
            task.running = true
            //获得调用的参数
            task.file = markRaw(this.api.file)
            task.params = markRaw({
                use_form: this.api.params.use_form,
                data: this.api.params.data,
                async: this.api.params.async,
                timeout: this.api.params.timeout,
                task_id: this.api.params.task_id,
            })
            //
            demo = false
            if (demo) {
                let t1 = new Date().getTime()
                task.status = 'uploading'
                for (const i of [1]) {
                    await utils.sleep(1000)
                    task.upload.total = 400
                    task.upload.loaded = 100 * i
                    task.upload.elapsed = new Date().getTime() - t1
                }
                task.status = 'executing'
                t1 = new Date().getTime()
                await utils.sleep(1000)
                task.execute.elapsed = new Date().getTime() - t1
                task.status = 'downloading'
                t1 = new Date().getTime()
                for (const i of [1]) {
                    await utils.sleep(1000)
                    task.download.total = 400
                    task.download.loaded = 100 * i
                    task.download.elapsed = new Date().getTime() - t1
                }
                //暂时模拟
                //const res = await fetch('./70.png.json')
                const res = await fetch('./demo.json')
                const data = await res.json()
                task.data = markRaw(data)
                task.running = false
                task.status = 'success'
                if (this.api.task.id == task.id) {
                    this.api.disabled = false
                }
            } else {
                task.handler = markRaw(new Handler(task))
                //有可能是异步轮训的
                try {
                    console.log('======>params', task.params.data)
                    new Api().request({
                        url: this.api.model.url,
                        file: this.api.file,
                        params: task.params.data,
                        useForm: task.params.use_form,
                        async: task.params.async,
                        timeout: task.params.timeout,
                        handler: task.handler
                    })
                    //等待完成
                    await task.handler.promise
                } catch (error) {
                    console.log(error)
                    task.status = 'error'
                    task.error = { 'code': 'api', 'message': '执行api失败' }
                } finally {
                    task.running = false
                    if (this.api.task.id == task.id) {
                        this.api.disabled = false
                    }
                }
            }
            console.log('end api')
        },
        async cancel() {
            //原来的任务并不需要设置任何
            this.api.disabled = false
            if (this.api.task && this.api.task.handler) {
                //如果是异步轮训结果的，可以取消轮训
                this.api.task.handler.cancel()
            }
            this.api.task = utils.newTask()
            //this.api.task.status='error'
            //this.api.task.error={'code':'cancelled','message':'任务被取消'}
            //this.api.task.data=null
        },
        download(event) {
            //如果是json的，就创建blob
            //这里使用的是utf-8编码

            function getFilename(name, blob) {
                let i = name.lastIndexOf('.')
                if (i != -1) {
                    name = name.substring(0, i)
                }
                if (blob.contentType == 'application/pdf') {
                    return `${name}.pdf`
                } else if (blob.contentType == 'application/zip') {
                    return `${name}.zip`
                } else if(blob.contentType =='application/json') {
                    return`${name}.json`
                }else{
                    return name
                }
            }
            let blob = null
            const data = this.api.task.data
            if (data instanceof Blob) {
                blob = data
            } else {
                blob = new Blob([JSON.stringify(data)])
                blob.contentType = 'application/json'
            }
            const filename = getFilename(this.api.task.file.name, blob)
            //const blob = new Blob(['中文的内容'])
            const url = URL.createObjectURL(blob)
            const el = event.target
            el.download = filename
            el.href = url
            setTimeout(() => {
                URL.revokeObjectURL(url)
            }, 1000)

        }
    }
}

const XSidebar = {
    template: '#x-sidebar',
    components: {
        'x-api': XApi,
        'x-task': XTask
    },
    props: [],
    emits: [],
    inject: ['state'],
    data() {
        return {
        }
    },

    computed: {
    },
    watch: {

    },
    methods: {


    },
    mounted() {
    }
}

const XIndex = {
    template: '#x-index',
    inject: ['state'],
    data() {
        return {}
    },
    methods: {

    }
}



const XPDFViewer = {
    template: '#x-pdf-viewer',
    props: ['file'],
    inject: ['state'],
    data() {
        return {

        }
    },

    computed: {
        url() {
            return URL.createObjectURL(this.file)
        },
        src() {
            if (this.state.pdfViewer == 'pdfjs') {
                return `./libs/pdfjs/web/viewer.html?file=${encodeURIComponent(this.url)}`
            } else {
                return this.url
            }
        },
        viewer() {
            return this.state.pdfViewer
        }
    },
    watch: {
        url(newValue, oldValue) {
            if (oldValue) {
                console.log('revoke object url', oldValue)
                URL.revokeObjectURL(oldValue)
            }
        }
    },
    methods: {},
    unmounted() {
        console.log('revoke object url', this.url)
        URL.revokeObjectURL(this.url)
    }
}
const XTextViewer = {
    template: '#x-text-viewer',
    props: ['file'],
    data() {
        return {
            //language:'plaintext',
            //languages:['plaintext','json','py'],
            wrap: true,
            text: '',
            total: 0,
            count: 0
        }
    },
    computed: {
        preview() {
            //太大就不预览了，避免浏览器卡死，默认为10M
            //return this.file.size <= 10 * 1024 * 1024
            return true
        }
    },
    watch: {
        async file(newValue) {
            const text = await newValue.text()
            this.setText(text)
        }
    },
    methods: {
        setText(text) {
            //太大的text会导致textarea崩溃，这里限制为10K
            //且不同:value或者 v-model绑定，而是手动设置value
            if (this.$refs.textarea) {
                //最多显示10K
                const s = text.substring(0, 1024 * 10)
                this.total = text.length
                this.count = s.length
                this.$refs.textarea.value = s
            }
        }
    },
    async mounted() {
        const text = await this.file.text()
        this.setText(text)
    }
}
const XCodeViewer = {
    template: '#x-code-viewer',
    props: ['file', 'language_'],
    data() {
        return {
            language: 'json',
            languages: ['plaintext', 'json'],
            theme: 'vs-dark',
            themes: ['vs-dark', 'hc-black', 'vs'],
            text: ''
        }
    },
    computed: {
        preview() {
            //太大就不预览了，默认为10M
            return this.file.size <= 10 * 1024 * 1024
        }
    },
    watch: {
        async file(newValue) {
            this.text = await newValue.text()
            if (this.editor) {
                this.editor.setValue(this.text)
                //会出现闪烁，最好的做法是先格式化，获得值再设置
                this.editor.trigger('', 'editor.action.formatDocument')
            }
        },
        theme(newValue) {
            monaco.editor.setTheme(newValue)
        },
        language(newValue) {
            if (this.editor) {
                //this.editor.setLanguage(newValue)
                var model = this.editor.getModel();
                monaco.editor.setModelLanguage(model, newValue)
            }
        }
    },
    methods: {
        async initEditor(text, theme, language) {
            return await new Promise((resolve, reject) => {
                //TODO 不需要再require了，因为已经存在了
                require(['vs/editor/editor.main'], () => {
                    //如果先添加缩进，就不会显示折叠了
                    //text = JSON.stringify(JSON.parse(text),null,2)
                    const editor = monaco.editor.create(this.$refs.body, {
                        value: text,
                        language: language,
                        theme: theme,
                    })
                    //editor.updateOptions({"autoIndent": true})
                    //console.log(editor)
                    //会出现闪烁，最好的做法是先格式化，获得值再设置
                    editor.trigger('', 'editor.action.formatDocument')
                    resolve(editor)
                }, reject);
            })
        },
        updateEditor(obj) {
            if (this.editor) {
                if (typeof obj == 'string') {
                    this.editor.setValue(obj)
                } else {
                    this.editor.setValue(obj ? JSON.stringify(obj) : '')
                }
                //会出现闪烁，最好的做法是先格式化，获得值再设置
                this.editor.trigger('', 'editor.action.formatDocument')
            }
        }
    },
    async mounted() {
        this.language = this.language_
        this.text = await this.file.text()
        this.editor = await this.initEditor(this.text, this.theme, this.language)
    }
}
const XImageViewer = {
    template: '#x-image-viewer',
    props: ['file'],
    data() {
        return {
            bgColor: '#ffffff',
            rotate: 0,
            scale: 1
        }
    },
    computed: {
        url() {
            return URL.createObjectURL(this.file)
        }
    },
    watch: {
        url(newValue, oldValue) {
            if (oldValue) {
                console.log('revoke object url', oldValue)
                URL.revokeObjectURL(oldValue)
            }
        }
    },
    methods: {

    },
    unmounted() {
        const url = this.url
        console.log('revoke object url', url)
        URL.revokeObjectURL(this.url)
    }
}
const XZipViewer = {
    template: '#x-zip-viewer',
    props: ['file'],
    components: {
        'x-text-viewer': XTextViewer,
        'x-image-viewer': XImageViewer
    },
    data() {
        return {
            types: [{ label: '文本', value: 'text' }, { label: '图片', value: 'image' }, { label: '未知', value: '' }],
            items: [],
            index: 0,
            blob: null,
            type: ''
        }
    },
    computed: {
        item() {
            if (this.index < this.items.length) {
                return this.items[this.index]
            } else {
                return null
            }
        }
    },
    watch: {
        async file(newValue) {
            this.items = await this.getItems(newValue)
            this.index = 0
        },
        async item(newValue) {
            if (newValue) {
                this.blob = await newValue.async('blob')
                const i = newValue.name.lastIndexOf('.')
                let ext = ''
                if (i >= 0) {
                    ext = newValue.name.substring(i + 1).toLowerCase()
                }
                if (['txt', 'json', 'yaml', 'yml', 'html', 'md'].includes(ext)) {
                    this.type = 'text'
                } else if (['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif'].includes(ext)) {
                    this.type = 'image'
                } else {
                    //也不需要再支持zip文件
                    //不需要再支持包含的pdf，虽然一样可以支持
                    this.type = ''
                }
            } else {
                this.blob = null
                this.type = ''
            }

        }
    },
    methods: {
        async getItems(file) {
            //解压zip
            const zip = new window.JSZip()
            await zip.loadAsync(file)
            const items = []
            zip.forEach((path, file) => {

                if (!file.dir && !path.startsWith('__MACOSX/')) {
                    items.push(file)
                } else {
                    //忽略目录
                    console.log('skip', path, file)
                }
            })
            return items
        }
    },
    async mounted() {
        this.items = await this.getItems(this.file)
    }
}

const XDocxViewer = {
    template: '#x-docx-viewer',
    props: ['file'],
    data() {
        return {
            zoom: 1
        }
    },
    computed: {

    },
    watch: {
        async file(newValue, oldValue) {
            await this.renderDocx(newValue)
        }
    },
    methods: {
        async renderDocx(file) {
            //console.log('============>nnnn',file,this.$refs.body)
            try {
                this.$refs.body.innerHTML = ''
                if (file) {
                    await window.docx.renderAsync(file, this.$refs.body)
                } else {
                    //this.$refs.body.innerHTML=''
                }
            } catch (e) {
                console.log(e)
            }
        },
        onDblClick(event) {
            //console.log(event)
            if (event.altKey) {
                if (event.shiftKey) {
                    //增加
                    this.zoom = Math.min(1, this.zoom + 0.2)
                } else {
                    //减少
                    this.zoom = Math.max(0.2, this.zoom - 0.2)
                }

            } else {
                //表示还原
                this.zoom = 1
            }

        },
        onMouseWheel(event) {

            if (event.altKey && event.deltaMode == 0) {
                event.preventDefault()
                //默认向下滚动为正
                //const y = event.deltaY*0.01
                this.zoom = Math.max(0.1, Math.min(2, this.zoom - event.deltaY * 0.01))
                //console.log(this.zoom,event)

            }
        }
    },
    async mounted() {
        await this.renderDocx(this.file)
    },
    unmounted() {

    }
}

const XOCRViewer = {
    template: '#x-ocr-viewer',
    props: ['task'],
    components: {
        //'vue-pdf-embed': window.VuePdfEmbed,
    },
    data() {
        //source={file:'',type:'pdf'}
        const scales = [
            {
                id: 1,
                name: 'auto',
                label: '自动'
            }, {
                id: 2,
                name: 'contains',
                label: '适合页面'
            }, {
                name: 'zoom',
                value: 0.5,
                label: '50%'
            }, {
                id: 3,
                name: 'zoom',
                value: 1,
                label: '100%'
            }, {
                id: 4,
                name: 'zoom',
                value: 1.5,
                label: '150%'
            }, {
                id: 5,
                name: 'zoom',
                value: 2,
                label: '200%'
            }
        ]
        return {
            //{name:'a.png',number:1,}  => 如果是来自pdf的，只有number，如果是来自zip，只有name
            index: 0,
            sourceVisible: false,
            //textVisible: true,
            //borderVisible: true,
            bgColor: '#ffffff',

            scaleSeq: 0,
            scale: scales[0],
            scales: markRaw(scales),

            //目前显示的html，目的是为了支持通过async的方式获得（使用watch）
            html: ''

        }
    },
    computed: {
        pages() {
            if (this.oldPages) {
                this.destroyPages(this.oldPages)
            }
            if (this.urlFile) {
                //如果存在了，就先释放
                this.urlFile.destroy().then(() => { })
                this.urlFile = null
            }
            this.oldPages = []

            function createPage(name, data) {
                return {
                    name: name,
                    width: data.width,
                    height: data.height,
                    rotate: data.rotate,
                    data: markRaw(data)
                }
            }
            const data = this.task.data
            const pages = []
            if (data.results) {
                //表示为zip
                for (const name in data.results) {
                    pages.push(createPage(name, data.results[name]))
                }
            } else if (data.pages) {
                //表示为pdf
                for (const pageData of data.pages) {
                    pages.push(createPage(`${pageData.number}`, pageData))
                }
            } else {
                //image，可以直接创建url
                pages.push(createPage('1', data))
            }
            //目的是在unmounted的时候来释放
            this.oldPages.push(...pages)
            return pages
        },
        page() {
            if (this.index < this.pages.length) {
                return this.pages[this.index]
            } else {
                return null
            }
        },
        bodyStyle() {
            return {
                'background-color': this.bgColor
            }
        },
        pageStyle() {
            return { '--scale': this.getScale() }
        }
    },
    methods: {
        async renderPage(page, sourceVisible) {
            //可以直接在这里指定scale:0.5
            //或者：scale:'--scale'
            let imageUrl = null
            if (sourceVisible) {
                imageUrl = await this.getImageUrl(page)
            } else {
                //
            }
            return new OCRPage().render(page.data, { scale: '--scale', imageUrl })
        },
        async getImageUrl(page) {
            const file = this.task.file
            const data = this.task.data
            let urlFile = this.urlFile
            if (!urlFile) {
                if (data.pages) {
                    urlFile = new PDF()
                } else if (data.results) {
                    urlFile = new ZIP()
                } else {
                    urlFile = new NativeFile()
                }
                await urlFile.load(file)
                this.urlFile = urlFile
                console.log('create url file', urlFile)
            } else {
                //如果需要快速释放资源，可以先如下
                //或者保留最后面的n个哈哈
                urlFile.revokeUrls()
            }
            //如果是pdf，为了更加清晰，可以选择scale=2
            return urlFile.getUrl(page.name, { rotate: page.rotate, scale: 2 })
        },
        getScale() {
            //表示依赖3个的变化
            this.page
            this.scale
            this.scaleSeq
            const el = this.$refs.body
            if (!el) {
                return 1
            }
            let width = el.clientWidth
            let height = el.clientHeight
            if (width == 0 || height == 0) {
                //可能为display:none
                return 1
            }
            //需要减去wrapper的padding，border等，可以通过计算，但是目前直接写死了
            width -= 30
            height -= 30
            let scale
            if (this.scale.name == 'zoom') {
                scale = this.scale.value
            } else if (this.scale.name == 'contains') {
                //完全包含
                const r1 = width / this.page.width
                const r2 = height / this.page.height
                scale = Math.min(r1, r2).toFixed(2)
                //不需要放大？
                //scale = min(scale,1)
            } else if (this.scale.name == 'auto') {
                const r1 = width / this.page.width
                const r2 = height / this.page.height
                scale = Math.max(r1, r2).toFixed(2)
                scale = Math.min(scale, 1)
            } else {
                //不支持的类型？
                scale = 1
            }
            return scale
        },
        onResize(entries) {
            console.log('onresize', entries, this.scaleSeq)
            //要求更新一下即可
            if (this.scale.name != 'zoom') {
                //如果当前为自动的，可以更新一下
                this.scaleSeq += 1
            }
        },
        destroyPages(pages) {
            console.log('==========>destroy pages')
            for (const page of pages) {
                //do nothing
            }
        }

    },
    watch: {
        scale(newValue) {
            console.log('=======>>scale', newValue)
        },
        pages(newValue, oldValue) {
            //console.log('============>pages')
            //this.destroyPages(oldValue)
        }
    },
    mounted() {
        console.log('======>>mounted')

        //目的是为了支持异步生成html，使用computed等必须为同步的
        this.$watch(() => {
            return [this.page, this.sourceVisible]
        }, async ([page, sourceVisible], oldValues) => {
            console.log('===>newValues', page, sourceVisible)
            this.html = await this.renderPage(page, sourceVisible)
        }, {
            immediate: true
        })
        let tid = 0
        //如果更新的耗时，可以设置为500等
        let delay = 0
        this.resizeObserver = new ResizeObserver((entries) => {
            if (delay > 0) {
                //console.log('onresizexxxx',tid)
                if (tid) {
                    clearTimeout(tid)
                }
                //延迟执行，避免太快
                tid = setTimeout(() => {
                    tid = 0
                    this.onResize(entries)
                }, delay)
            } else {
                this.onResize(entries)
            }
        })
        this.resizeObserver.observe(this.$refs.body)

        //需要依赖el元素计算，所以在这里设置一值
        //const scale = this.getScale()
        //utils.setCssValue(this.$refs.wrapper,'--scale',scale)
        this.scaleSeq += 1

    },
    beforeUnmount() {
        console.log('===========>>beforeunmounted', this.$refs.body)
        if (this.resizeObserver) {
            this.resizeObserver.unobserve(this.$refs.body)
        }
    },
    async unmounted() {
        console.log('========>unmounted')
        //这里不应该再调用this.pages，然后这个时候task状态已经改变了，或者是一个新的task
        this.destroyPages(this.oldPages)
        this.oldPages = []
        if (this.urlFile) {
            await this.urlFile.destroy()
            this.urlFile = null
        }
    },
    updated() {
        console.log('updated')
    }
}

const XDetectViewer = {
    template: '#x-detect-viewer',
    props: ['task'],
    components: {
        //'vue-pdf-embed': window.VuePdfEmbed,
    },
    data() {
        //source={file:'',type:'pdf'}
        const scales = [
            {
                id: 1,
                name: 'auto',
                label: '自动'
            }, {
                id: 2,
                name: 'contains',
                label: '适合页面'
            }, {
                name: 'zoom',
                value: 0.5,
                label: '50%'
            }, {
                id: 3,
                name: 'zoom',
                value: 1,
                label: '100%'
            }, {
                id: 4,
                name: 'zoom',
                value: 1.5,
                label: '150%'
            }, {
                id: 5,
                name: 'zoom',
                value: 2,
                label: '200%'
            }
        ]
        let models = []
        if (this.task.data.format == 'pdf') {
            models = [...this.task.data.models]
        } else if (this.task.data.format == 'zip') {
            models = [...this.task.data.models]
        } else {
            //图片上传
            for (const key in this.task.data.models) {
                models.push(key)
            }
        }

        console.log('=================>>>>models', models)
        return {
            //{name:'a.png',number:1,}  => 如果是来自pdf的，只有number，如果是来自zip，只有name
            index: 0,
            sourceVisible: true,
            //textVisible: true,
            //borderVisible: true,
            bgColor: '#ffffff',

            scaleSeq: 0,
            scale: scales[0],
            scales: markRaw(scales),

            model: models[0],
            models: models,
            //目前显示的html，目的是为了支持通过async的方式获得（使用watch）
            html: ''

        }
    },
    computed: {
        models2() {
            const models = []
            for (const key in this.task.data.models) {
                models.push(key)
            }
            return models
        },
        pages() {
            if (this.oldPages) {
                this.destroyPages(this.oldPages)
            }
            if (this.urlFile) {
                //如果存在了，就先释放
                this.urlFile.destroy().then(() => { })
                this.urlFile = null
            }
            this.oldPages = []

            const that = this
            function createPage(name, data) {
                //获得当前model对应的数据
                data = data.models[that.model]
                return {
                    name: name,
                    width: data.width,
                    height: data.height,
                    rotate: data.rotate,
                    model: that.model,
                    data: markRaw(data)
                }
            }
            const data = this.task.data
            const pages = []
            if (data.results) {
                //表示为zip
                for (const name in data.results) {
                    pages.push(createPage(name, data.results[name]))
                }
            } else if (data.pages) {
                //表示为pdf
                for (const pageData of data.pages) {
                    pages.push(createPage(`${pageData.number}`, pageData))
                }
            } else {
                //image，可以直接创建url
                pages.push(createPage('1', data))
            }
            //目的是在unmounted的时候来释放
            this.oldPages.push(...pages)
            return pages
        },
        page() {
            if (this.index < this.pages.length) {
                return this.pages[this.index]
            } else {
                return null
            }
        },
        bodyStyle() {
            return {
                'background-color': this.bgColor
            }
        },
        pageStyle() {
            return { '--scale': this.getScale() }
        }
    },
    methods: {
        async renderPage(page, sourceVisible) {
            //可以直接在这里指定scale:0.5
            //或者：scale:'--scale'
            let imageUrl = null
            if (sourceVisible) {
                imageUrl = await this.getImageUrl(page)
            } else {
                //
            }
            return new DetectPage().render(page.data, { scale: '--scale', imageUrl })

        },
        async getImageUrl(page) {
            const file = this.task.file
            const data = this.task.data
            let urlFile = this.urlFile
            if (!urlFile) {
                if (data.pages) {
                    urlFile = new PDF()
                } else if (data.results) {
                    urlFile = new ZIP()
                } else {
                    urlFile = new NativeFile()
                }
                await urlFile.load(file)
                this.urlFile = urlFile
                console.log('create url file', urlFile)
            } else {
                //如果需要快速释放资源，可以先如下
                //或者保留最后面的n个哈哈
                urlFile.revokeUrls()
            }
            //如果是pdf，为了更加清晰，可以选择scale=2
            return urlFile.getUrl(page.name, { rotate: page.rotate, scale: 2 })
        },
        getScale() {
            //表示依赖3个的变化
            this.page
            this.scale
            this.scaleSeq
            const el = this.$refs.body
            if (!el) {
                return 1
            }
            let width = el.clientWidth
            let height = el.clientHeight
            //let width = el.offsetWidth
            //let height = el.offsetHight
            console.log('======>>>ssss', width, height)
            if (width == 0 || height == 0) {
                //可能为display:none
                return 1
            }
            //需要减去wrapper的padding，border等，可以通过计算，但是目前直接写死了
            width -= 40
            height -= 40
            let scale
            if (this.scale.name == 'zoom') {
                scale = this.scale.value
            } else if (this.scale.name == 'contains') {
                //完全包含
                const r1 = width / this.page.width
                const r2 = height / this.page.height
                scale = Math.min(r1, r2).toFixed(2)
                //不需要放大？
                //scale = min(scale,1)
            } else if (this.scale.name == 'auto') {
                const r1 = width / this.page.width
                const r2 = height / this.page.height
                scale = Math.max(r1, r2).toFixed(2)
                //scale= Math.min(r1,r2).toFixed(2)
                scale = Math.min(scale, 1)
            } else {
                //不支持的类型？
                scale = 1
            }
            return scale
        },
        onResize(entries) {
            console.log('onresize', entries, this.scaleSeq, this.scale.name)
            //要求更新一下即可
            if (this.scale.name != 'zoom') {
                //如果当前为自动的，可以更新一下
                this.scaleSeq += 1
            }
        },
        destroyPages(pages) {
            console.log('==========>destroy pages')
            for (const page of pages) {
                //do nothing
            }
        }

    },
    watch: {
        scale(newValue) {
            console.log('=======>>scale', newValue)
        },
        pages(newValue, oldValue) {
            //console.log('============>pages')
            //this.destroyPages(oldValue)
        }
    },
    mounted() {
        console.log('======>>mounted')

        //目的是为了支持异步生成html，使用computed等必须为同步的
        this.$watch(() => {
            return [this.page, this.sourceVisible]
        }, async ([page, sourceVisible], oldValues) => {
            console.log('===>newValues', page, sourceVisible)
            this.html = await this.renderPage(page, sourceVisible)
        }, {
            immediate: true
        })
        let tid = 0
        //如果更新的耗时，可以设置为500等
        let delay = 0
        this.resizeObserver = new ResizeObserver((entries) => {
            if (delay > 0) {
                //console.log('onresizexxxx',tid)
                if (tid) {
                    clearTimeout(tid)
                }
                //延迟执行，避免太快
                tid = setTimeout(() => {
                    tid = 0
                    this.onResize(entries)
                }, delay)
            } else {
                this.onResize(entries)
            }
        })
        this.resizeObserver.observe(this.$refs.body)

        //需要依赖el元素计算，所以在这里设置一值
        //const scale = this.getScale()
        //utils.setCssValue(this.$refs.wrapper,'--scale',scale)
        this.scaleSeq += 1

    },
    beforeUnmount() {
        console.log('===========>>beforeunmounted', this.$refs.body)
        if (this.resizeObserver) {
            this.resizeObserver.unobserve(this.$refs.body)
        }
    },
    async unmounted() {
        console.log('========>unmounted')
        //这里不应该再调用this.pages，然后这个时候task状态已经改变了，或者是一个新的task
        this.destroyPages(this.oldPages)
        this.oldPages = []
        if (this.urlFile) {
            await this.urlFile.destroy()
            this.urlFile = null
        }
    },
    updated() {
        console.log('updated')
    }
}

const XJsonViewer = {
    template: '#x-json-viewer',
    props: ['task'],
    data() {
        return {
            language: 'json',
            languages: ['json'],
            theme: 'vs',
            themes: ['vs-dark', 'hc-black', 'vs']
        }
    },
    computed: {
        text() {
            return JSON.stringify(this.task.data)
        },
        preview() {
            //太大就不预览了，默认为10M
            return this.file.size <= 10 * 1024 * 1024
        }
    },
    watch: {
        theme(newValue) {
            monaco.editor.setTheme(newValue)
        },
        language(newValue) {
            if (this.editor) {
                //this.editor.setLanguage(newValue)
                var model = this.editor.getModel();
                monaco.editor.setModelLanguage(model, newValue)
            }
        }
    },
    methods: {
    },
    async mounted() {
        const editor = monaco.editor.create(this.$refs.body, {
            value: this.text,
            theme: this.theme,
            language: 'json',
            minimap: {
                enabled: false
            },
            automaticLayout: true,
            scrollBeyondLastLine: false,
            readOnly: false
        })
        //editor.updateOptions({"autoIndent": true})
        //console.log(editor)
        //会出现闪烁，最好的做法是先格式化，获得值再设置
        editor.trigger('', 'editor.action.formatDocument')

        this.editor = editor
    },
    unmounted() {
        if (this.editor) {
            this.editor.dispose()
        }
    }
}

const XFile = {
    template: '#x-file',
    inject: ['state'],
    props: [],
    components: {
        'x-pdf-viewer': XPDFViewer,
        'x-image-viewer': XImageViewer,
        'x-zip-viewer': XZipViewer,
        'x-text-viewer': XTextViewer,
        'x-code-viewer': XCodeViewer,
        'x-docx-viewer': XDocxViewer
    },
    data() {
        return {}
    },
    computed: {
        file() {
            return this.state.api.file
        },
        pdfViewer() {
            return this.state.pdfViewer
        },
        ext() {
            const name = this.file.name
            const i = name.lastIndexOf('.')
            let ext
            if (i != -1) {
                //abc.pdf => pdf
                ext = name.substring(i + 1)
            } else {
                ext = ''
            }
            return ext.toLowerCase()
        },
        isImage() {
            return ['png', 'webp', 'bmp', 'gif', 'jpg', 'jpeg'].includes(this.ext)
        },
        isPDF() {
            return ['pdf'].includes(this.ext)
        },
        isZIP() {
            return ['zip'].includes(this.ext)
        },
        isText() {
            return ['txt', 'json', 'html', 'yaml', 'yml', 'md'].includes(this.ext)
        },
        isDocx() {
            return ['docx'].includes(this.ext)
        }
    },
    methods: {

    }
}

const XResult = {
    template: '#x-result',
    inject: ['state'],
    props: [],
    components: {
        'x-ocr-viewer': XOCRViewer,
        'x-detect-viewer': XDetectViewer,
        'x-pdf-viewer': XPDFViewer,
        'x-json-viewer': XJsonViewer
    },
    data() {
        return {}
    },
    computed: {
        api() {
            return this.state.api
        }
    }
}

const XPreview = {
    template: '#x-preview',
    inject: ['state'],
    components: {
        'x-file': XFile,
        'x-result': XResult
    },
    data() {
        return {
            leftVisible: true,
            rightVisible: true
        }
    },
    methods: {
        toggleLeft() {
            if (!this.rightVisible) {
                this.rightVisible = true
            } else {
                this.leftVisible = false
            }
        },
        toggleRight() {
            if (!this.leftVisible) {
                this.leftVisible = true
            } else {
                this.rightVisible = false
            }
        }
    }
}

const XMain = {
    template: '#x-main',
    props: ['api', 'viewer', 'pdfViewer'],
    components: {
        'x-file': XFile,
        'x-result': XResult
    },
    data() {
        return {}
    }
}

const XParams = {
    template: '#x-params',
    inject: ['state'],
    props: [],
    components: {
        //'vue-form': window.vue3FormElement.default
    },
    data() {
        return {
            theme: 'vs',
            themes: ['vs', 'vs-dark', 'hc-black'],
            rightModelName: 'defaults'
        }
    },
    watch: {
        theme(newValue, oldValue) {
            //console.log(newValue)
            monaco.editor.setTheme(newValue)
        },
    },
    computed: {
        api() {
            return this.state.api
        },
        params() {
            return this.state.api.params
        }
    },
    methods: {
        onSubmit(formData) {
            //console.log('formdata', formData)
        },
        async createEditor(el, model) {
            const editor = monaco.editor.create(el, {
                value: text,
                language: language,
                theme: theme,
                automaticLayout: true,
                minimap: {
                    enabled: false
                },
                readOnly: false
            })
        },
    },

    async mounted() {
        if (false) {
            console.log('mounted', this.$refs.jsonEditor)
            //const container = document.querySelector('#jsoneditor-dialog .x-jsoneditor')
            const container = this.$refs.container
            const options = {
                modes: ['text', 'code', 'tree', 'preview'],
                mode: 'code'
            }
            const editor = new JSONEditor(container, options)
            editor.set(this.api.file.params)
            editor.setSchema({})
        }


        //
        if (false) {
            let editor = ace.edit(this.$refs.editor);
            //editor2.setTheme("ace/theme/monokai");
            editor.session.setMode("ace/mode/json");
            editor.setValue('{}')

            let editor2 = ace.edit(this.$refs.defaultEditor);
            //editor2.setTheme("ace/theme/monokai");
            editor2.session.setMode("ace/mode/json5");
            editor2.setValue('{"a":1,"b":3 //xyz}')
            editor2.setReadOnly(true);
        }
        if (true) {

            //require.config({ paths: { vs: './node_modules/monaco-editor/min/vs' } })
            //const editor = await this.createEditor(this.$refs.editor, '{}', 'json', 'vs')
            //const editor2 = await this.createEditor(this.$refs.defaultEditor, '{}', 'json', 'vs', { readOnly: true })

            //const uri = monaco.Uri.parse(`memect://${this.api.url}/${this.api.file.type}/params.json`)
            const uri = this.params.monaco.uri
            const model = monaco.editor.createModel(JSON.stringify(this.params.data || {}), "json", uri);
            //const defaultModel = monaco.editor.createModel(JSON.stringify({}), "json", uri);
            const editor = monaco.editor.create(this.$refs.editor, {
                model: model,
                automaticLayout: true,
                minimap: {
                    enabled: false
                },
                scrollBeyondLastLine: false,
                readOnly: false
            })
            model.onDidChangeContent((e) => {
                console.log('change', e, model.getValue())
                //因为无法单独设置某个编辑器的json支持注释，必须全局配置，所以
                //这里获得的json可能有注释，使用json5解析
                try {
                    this.params.data = JSON.parse(model.getValue())
                } catch (e) {
                    //忽略错误
                }
            })
            const editor2 = monaco.editor.create(this.$refs.defaultEditor, {
                value: '{}',
                language: 'json',
                automaticLayout: true,
                minimap: {
                    enabled: false
                },
                scrollBeyondLastLine: false,
                readOnly: true
            })

            this.editor = editor
            this.defaultEditor = editor2
            this.model = model
            //this.defaultModel = defaultModel
        }
    },
    unmounted() {

        if (this.editor) {
            console.log('dispose1')
            this.editor.dispose()
        }
        if (this.defaultEditor) {
            console.log('dispose2')
            this.defaultEditor.dispose()
        }
        if (this.model) {
            //手动构造的model必须手动释放
            this.model.dispose()
        }

        if (this.defaultModel) {
            this.defaultModel.dispose()
        }
    }
}


const XSystem = {
    template: '#x-system',
    inject: ['state'],
    data() {
        return {}
    }
}


class Tag {
    constructor(name) {
        this.name = name
        this.classes = []
        this.style = {}
        this.attrs = {}
        this.data = {}
        this.children = []
    }
    toHtml() {
        const buf = []
        this.write(buf)
        return buf.join('')
    }
    write(buf) {
        //<div></div>
        buf.push('<')
        buf.push(this.name)
        if (this.classes.length > 0) {
            buf.push(` class="${this.classes.join(' ')}"`)
        }
        if (!utils.isEmpty(this.style)) {
            //style="left:10px;"
            buf.push(` style="${this.writeStyle(this.style)}"`)
        }

        if (!utils.isEmpty(this.attrs)) {
            //a="1" b="2"
            for (const k in this.attrs) {
                const v = utils.escapeHtml(this.attrs[k])
                buf.push(` ${k}="${v}"`)
            }

        }
        if (!utils.isEmpty(this.data)) {
            //data-a="" data-b=""
            for (const k in this.data) {
                const v = utils.escapeHtml(this.data[k])
                buf.push(` ${k}=${v}`)
            }
        }

        buf.push('>')
        for (const child of this.children) {
            if (typeof child == 'string') {
                //html
                buf.push(child)
            } else {
                //tag
                buf.push(child.toHtml())
            }
        }
        buf.push('</')
        buf.push(this.name)
        buf.push('>')
    }

    writeStyle(style) {
        const buf = []
        for (const k in style) {
            const v = style[k]
            buf.push(`${k}:${v}`)
        }
        //left:10px;right:20px
        return buf.join(';')
    }

}
class OCRPage {
    constructor() {

    }
    render(result, { scale = 1, imageUrl = null }) {
        this.scale = scale
        const width = result['width']
        const height = result['height']
        const div = new Tag('div')
        div.classes.push('x-ocr-page')
        div.style['width'] = this.getNumber(width)
        div.style['height'] = this.getNumber(height)

        if (imageUrl) {
            //添加，表示当前显示了背景图片
            div.classes.push('x-ocr-page-bg')
            div.style['background-image'] = `url('${imageUrl}')`
            div.style['background-position'] = 'center'
            div.style['background-size'] = 'contain'
            div.style['background-repeat'] = 'no-repeat'
        } else {
            //白色背景颜色
            div.style['background-color'] = '#ffffff'
        }

        const spans = result['spans']
        const tags = [];
        for (const span of spans) {
            tags.push(this.renderSpan(span))
        }
        div.children.push(...tags)
        return div.toHtml()
    }
    getNumber(value) {
        if (typeof this.scale == 'string') {
            //表示使用css变量，通过改变css变量即可，也可以全局使用zoom
            //'calc(var(--scale) * {v}px)'
            return `calc(var(${this.scale}) * ${value}px)`
        } else {
            //四舍五入或者保留2位？
            value = Math.round(value * this.scale)
            return `${value}px`
        }
    }
    renderSpan(span) {
        //计算坐标和旋转
        function getFontSize(text, width, height, isVeritcal) {
            //仅仅考虑最常见的字体，也就是字符的width<=height
            if (isVeritcal) {
                //认为是垂直书写了，使用这个如果是“😄”，返回2，因为是一个char组成
                //let n = text.length
                let n = 0
                for (let c of text) {
                    //如果是英文，垂直书写的时候，等同于顺时针翻转90度，只占据0.5
                    if(/[\u0000-\u00ff]/.test(c)){
                        n+=0.6
                    }else{
                        n+=1
                    }
                }
                return (height / n).toFixed(2)
            } else {
                let n = 0
                for (let c of text) {
                    if (/[\u0000-\u00ff]/.test(c)) {
                        n += 0.6
                    } else {
                        n += 1
                    }
                }
                //再和高度相比较，需要小于高度
                const k1 = (width / n).toFixed(2)
                const k2 = Math.max(2,height-1)
                return Math.min(k1,k2)
            }
        }
        function getRotate(x1, y1, x2, y2, x3, y3, x4, y4) {
            //[left-top,right-top,right-bottom,left-bottom]
            //原点为左上角
            //可以计算出来旋转的度数，但是无法支持90，270，180等
            //0度，可能是水平书写，或者可能垂直书写
            //如：90度，表示文字原来是水平书写的，然后顺时针旋转90度
            const cx1 = (x4 + x1) / 2
            const cy1 = (y4 + y1) / 2
            const cx = (x1 + x3) / 2
            const cy = (y1 + y3) / 2
            const d = cx - cx1
            if (d == 0) {
                return 0
            }
            return Math.atan((cy - cy1) / d)
        }
        function calcBBox(points) {
            const [x1, y1] = points[0]
            const [x2, y2] = points[1]
            const [x3, y3] = points[2]
            const [x4, y4] = points[3]
            //获得中心点
            const cx = (x1 + x3) / 2
            const cy = (y1 + y3) / 2
            const cx1 = (x1 + x4) / 2
            const cy1 = (y1 + y4) / 2
            const cx2 = (x1 + x2) / 2
            const cy2 = (y1 + y2) / 2

            //计算度数
            //正表示顺时针
            const rotateRadian = getRotate(x1, y1, x2, y2, x3, y3, x4, y4)
            const rotate = rotateRadian * 180 / Math.PI

            //计算旋转正确后x1,y1等
            const sin = Math.sin(rotateRadian)
            const sin2 = Math.sin(Math.PI / 2 - rotateRadian)
            const radius = sin == 0 ? (cx - cx1) : (cy - cy1) / sin
            const radius2 = sin2 == 0 ? (cx2 - cx) / 2 : (cy - cy2) / sin2
            let left = cx - radius
            let top = cy - radius2
            let width = 2 * radius
            let height = 2 * radius2

            //console.log('=======>>>', span.text, rotate, sin, sin2, radius, radius2, [[x1, y1], [x2, y2], [x3, y3], [x4, y4]], [left, top, width, height])
            return { left, top, width, height, rotate }
        }
        function isVerticalWrite(text, width, height) {
            let n = 0
            for (let c of text) {
                n += 1
            }
            if (n < 2) {
                return false
            }
            return height >= width * 1.5
        }
        const points = span['points']
        const score = span['score']
        const text = span['text']
        //如果span本身给出旋转度数，就不需要计算，现在还没有
        if (span['rotate'] !== null && span['rotate'] !== undefined) {
            //表示给出了rotate，相对水平
            //然后计算出未旋转的位置，就可以准确计算fontsize，且可以正确的显示书写
            //和字体
        } else {
            //计算旋转度数，和字体

        }

        const { left, top, width, height, rotate } = calcBBox(points)
        const isVeritcal = isVerticalWrite(text, width, height)
        const fontSize = getFontSize(text, width, height, isVeritcal)
        //let scale = width/(fontSize*text.length)
        const tag = new Tag('span')
        tag.classes.push('x-ocr-page-span')
        //或者使用css变量，后续只需要动态改变scale的css值即可
        //calc(var(--scale) * {v}px)
        tag.style['left'] = this.getNumber(left)
        tag.style['top'] = this.getNumber(top)
        tag.style['width'] = this.getNumber(width)
        tag.style['height'] = this.getNumber(height)
        

        if (isVeritcal) {
            //简单这样即可，虽然不100%还原，因为字母数字可能为旋转书写
            tag.style['writing-mode'] = 'vertical-lr'
            tag.style['text-align']='center'
        }else{
            tag.style['line-height']=this.getNumber(height)
            //tag.style['text-align']='center'
        }

        //tag.style['transform']=`scaleX(${scale})`

        tag.style['font-size'] = this.getNumber(fontSize)
        //处理旋转
        if (rotate != 0) {
            tag.style['transform-origin'] = 'center'
            tag.style['transform'] = `rotate(${rotate}deg)`
        }

        tag.attrs['title'] = `score=${score}`

        //const textTag = new Tag('span')
        //textTag.style['line-height']=`${height}px`
        //textTag.children.push(escapeHtml(text))

        tag.children.push(utils.escapeHtml(text))

        return tag
    }
}

//
class DetectPage {
    constructor() {

    }
    render(result, { scale = 1, imageUrl = null }) {
        console.log('===========>result', result)
        this.scale = scale
        const width = result['width']
        const height = result['height']
        const div = new Tag('div')
        div.classes.push('x-detect-page')
        div.style['width'] = this.getNumber(width)
        div.style['height'] = this.getNumber(height)

        if (imageUrl) {
            //添加，表示当前显示了背景图片
            div.classes.push('x-detect-page-bg')
            div.style['background-image'] = `url('${imageUrl}')`
            div.style['background-position'] = 'center'
            div.style['background-size'] = 'contain'
            div.style['background-repeat'] = 'no-repeat'
        } else {
            //白色背景颜色
            div.style['background-color'] = '#ffffff'
        }

        let objects = result['objects']
        const tags = [];
        for (let i = 0; i < objects.length; i++) {
            tags.push(this.renderObject(i, objects))
        }
        div.children.push(...tags)
        return div.toHtml()
    }
    getNumber(value) {
        if (typeof this.scale == 'string') {
            //表示使用css变量，通过改变css变量即可，也可以全局使用zoom
            //'calc(var(--scale) * {v}px)'
            return `calc(var(${this.scale}) * ${value}px)`
        } else {
            //四舍五入或者保留2位？
            value = Math.round(value * this.scale)
            return `${value}px`
        }
    }
    renderObject(i, objects) {
        //计算坐标和旋转
        //let scale = width/(fontSize*text.length)
        const obj = objects[i]
        const tag = new Tag('div')
        tag.classes.push('x-detect-page-object')
        //或者使用css变量，后续只需要动态改变scale的css值即可
        //calc(var(--scale) * {v}px)
        //没有表示为分类
        const [x0, y0, x1, y1] = obj.bbox || [10, 10 + 30 * i, 200, 10 + 30 * (i + 1)]
        tag.style['left'] = this.getNumber(x0)
        tag.style['top'] = this.getNumber(y0)
        tag.style['width'] = this.getNumber(x1 - x0)
        tag.style['height'] = this.getNumber(y1 - y0)

        //tag.attrs['title'] = `score=${obj.score}`

        const textTag = new Tag('span')
        textTag.children.push(utils.escapeHtml(`${obj.type} ${obj.score}`))

        tag.children.push(textTag)

        return tag

    }
}

class Handler {
    constructor(task) {
        this.cancelled = false
        this.tid = null
        this.task = task
        this.uploadStartTime = null
        this.downloadStartTime = null
        this.executeStartTime = null
        this.done = false

        this.promise = new Promise((resolve, reject) => {
            this.resolve = resolve
            this.reject = reject
        })
    }
    updateExecute() {
        if (this.done || !this.task || this.task.status != 'executing') {
            return
        }
        this.task.execute.elapsed = new Date().getTime() - this.executeStartTime
        setTimeout(() => this.updateExecute(), 1000)
    }
    onDone(error, data) {
        //如果已经调用过了，如：正常的结束，或者取消了轮训
        if (this.done) {
            return
        }
        this.done = true
        if (this.task) {
            if (error) {
                this.task.status = 'error'
                this.task.error = error
            } else {
                this.task.status = 'success'
                this.task.data = markRaw(data)
            }
        }

        this.resolve(null)
    }
    onUploadStart(enable, loaded, total) {
        console.log('start upload')
        this.uploadStartTime = new Date().getTime()
        if (!this.task) {
            return
        }
        this.task.status = 'uploading'
        this.task.upload.elapsed = 0
        if (enable) {
            this.task.upload.loaded = loaded
            this.task.upload.total = total
        }

    }
    onUploadProgress(enable, loaded, total) {
        if (!this.task) {
            return
        }
        this.task.upload.elapsed = new Date().getTime() - this.uploadStartTime
        if (enable) {
            this.task.upload.loaded = loaded
            this.task.upload.total = total
            this.task.elapsed = ''
        }

    }
    onUploadEnd(enable, loaded, total) {
        console.log('end upload')
        this.executeStartTime = new Date().getTime()
        if (!this.task) {
            return
        }
        this.task.upload.elapsed = new Date().getTime() - this.uploadStartTime
        if (enable) {
            this.task.upload.loaded = loaded
            this.task.upload.total = total
        }
        this.task.status = 'executing'
        this.task.execute.elapsed = 0
        //就需要启动一个计算器来更新execute的耗时了
        this.updateExecute()

    }
    onDownloadStart(enable, loaded, total) {
        console.log('start download', loaded, total)
        this.downloadStartTime = new Date().getTime()
        if (!this.task) {
            return
        }
        this.task.execute.elapsed = new Date().getTime() - this.executeStartTime
        this.task.status = 'downloading'
        this.task.download.elapsed = 0
        if (enable) {
            this.task.download.loaded = loaded
            this.task.download.total = total
        }
    }
    onDownloadProgress(enable, loaded, total) {
        console.log('progress download', enable, loaded, total)
        if (!this.task) {
            return
        }
        if (this.task.status == 'executing') {
            this.downloadStartTime = new Date().getTime()
            this.task.execute.elapsed = new Date().getTime() - this.executeStartTime
            this.task.status = 'downloading'
            this.task.download.elapsed = 0
        } else {

        }
        this.task.download.elapsed = new Date().getTime() - this.downloadStartTime
        if (enable) {
            this.task.download.loaded = loaded
            this.task.download.total = total
        }
    }
    onDownloadEnd(enable, loaded, total) {
        console.log('end download')
        if (!this.task) {
            return
        }
        this.task.download.elapsed = new Date().getTime() - this.downloadStartTime
        if (enable) {
            this.task.download.loaded = loaded
            this.task.download.total = total
        }
    }

    setTimeout(fn, delay) {
        if (this.tid) {
            clearTimeout(this.tid)
            this.tid = null
        }

        if (this.cancelled) {
            return
        }
        this.tid = setTimeout(fn, delay)
    }
    cancel() {
        if (this.tid) {
            clearTimeout(this.tid)
            this.tid = null
        }
        this.cancelled = true
        this.onDone({ 'code': 'cancel poll', 'message': '取消了轮训结果' })
        if (this.xhr) {
            //对于大模型的请求，关闭连接，可以让后台停止生成
            console.log('abort xhr')
            this.xhr.abort()
            this.xhr = null
        }
    }

}


class Api {
    constructor() {

    }
    request({ url, file, params, useForm = false, async = false, timeout = null, taskId = null, handler = null }) {
        //使用fetch无法获得上传进度
        const xhr = new XMLHttpRequest()


        if (!handler) {
            handler = Handler()
        }

        //暂时如此设置，目的是可以取消
        handler.xhr = xhr

        function pollResult(task) {
            //这里可以使用fetch
            let pollUrl = makeURL(url, new URLSearchParams({ task_id: task.id, '_': new Date().getTime() }))
            const xhr = new XMLHttpRequest()
            xhr.responseType = 'arraybuffer'

            xhr.onloadstart = (event) => {
                //这个open的时候就调用了，不是接收到数据，必须使用如下替代
            }
            xhr.onreadystatechange = (event) => {
                //console.log('state',xhr.readyState)
                if (xhr.readyState == XMLHttpRequest.LOADING) {
                    //这个事件会发出多次

                }
            }
            xhr.onprogress = (event) => {
                //如果已经为执行成功
                const status = xhr.getResponseHeader('x-api-status')
                if (xhr.status == 200 && status == 'success') {
                    handler.onDownloadProgress(event.lengthComputable, event.loaded, event.total)
                }
            }
            xhr.onloadend = (event) => {
                const status = xhr.getResponseHeader('x-api-status')
                if (xhr.status == 200 && status == 'success') {
                    //handler.onDownloadProgress(event.lengthComputable, event.loaded, event.total)
                    handler.onDownloadEnd(event.lengthComputable, event.loaded, event.total)
                }
                if (xhr.status == 200) {
                    if (xhr.getResponseHeader('x-api-result') == 'binary') {
                        //表示成功且返回二进制数据
                        handler.onDone(null, toBlob(xhr.response, xhr.getResponseHeader('content-type')))
                    } else {
                        //失败/成功，返回的都是json格式
                        const result = toJson(xhr.response)
                        const error = result['error']
                        if (error) {
                            //正在执行，继续轮训
                            //1秒钟轮训一次
                            if (error['code'] == 'running') {
                                //如果handler被取消了，就不会再轮训了
                                handler.setTimeout(() => {
                                    pollResult(task)
                                }, 1000)
                            } else {
                                //有错误
                                handler.onDone(error, null)
                            }

                        } else {
                            //没有错误，接收结果
                            handler.onDone(null, parseResult(result))
                        }
                    }

                } else {
                    //轮训失败
                    handler.onDone({ 'code': 'http', 'message': `status=${xhr.status}` })
                }
            }
            xhr.open('GET', pollUrl)
            xhr.send()
        }
        function setListeners() {
            xhr.upload.onloadstart = (event) => {
                handler.onUploadStart(event.lengthComputable, event.loaded, event.total)
            }
            xhr.upload.onprogress = (event) => {
                //上传进度
                handler.onUploadProgress(event.lengthComputable, event.loaded, event.total)
            }
            xhr.upload.onloadend = (event) => {
                //成功
                handler.onUploadEnd(event.lengthComputable, event.loaded, event.total)
            }

            if (!async) {
                //异步的不需要
                xhr.onloadstart = (event) => {
                    //还没有上传的时候就先执行了这个
                    //handler.onDownloadStart(event.lengthComputable, event.loaded, event.total)
                }
                xhr.onreadystatechange = (event) => {
                    //console.log('state',xhr.readyState)
                    if (xhr.readyState == XMLHttpRequest.LOADING) {
                        //这个事件会发出多次，就不监听了
                        //handler.onDownloadStart(true, 0, 0)
                    }
                }
                //下载进度
                xhr.onprogress = (event) => {
                    //仅仅监听这个即可
                    //handler.onDownloadProgress(event.lengthComputable, event.loaded, event.total)
                    const status = xhr.getResponseHeader('x-api-status')
                    if (xhr.status == 200 && status == 'success') {
                        handler.onDownloadProgress(event.lengthComputable, event.loaded, event.total)
                    }
                }
            }


            xhr.onloadend = (event) => {
                const status = xhr.getResponseHeader('x-api-status')
                if (xhr.status == 200 && status == 'success') {
                    //handler.onDownloadProgress(event.lengthComputable, event.loaded, event.total)
                    handler.onDownloadEnd(event.lengthComputable, event.loaded, event.total)
                }
                if (xhr.status == 200) {
                    //执行完毕
                    let data = null
                    let error = null
                    if (xhr.getResponseHeader('x-api-result') == 'binary') {
                        //如果返回的是二进制，表示成功了，只有成功才有可能返回二进制
                        data = toBlob(xhr.response, xhr.getResponseHeader('content-type'))
                    } else {
                        //返回json，成功或者失败
                        const result = toJson(xhr.response)
                        if (result['error']) {
                            error = result['error']
                        } else {
                            //如果有result.base64==true，就需要使用base64解码为
                            data = parseResult(result)
                        }
                    }

                    if (error) {
                        handler.onDone(error, null)
                    } else {
                        if (async) {
                            //轮训结果
                            pollResult(data)
                        } else {
                            //如果有result.base64==true，就需要使用base64解码为
                            handler.onDone(null, data)
                        }
                    }

                } else {
                    //404表示url不存在
                    //405表示api不存在（也就是post了错误的url）
                    //500系统错误
                    //0表示网络错误，abort/timeout等
                    handler.onDone({ 'code': 'http', 'message': `status=${xhr.status}` }, null)
                }
            }


        }
        function makeURL(url, query) {
            const qs = query.toString()
            if (qs.length > 0) {
                if (url.indexOf('?') != -1) {
                    url += '&' + qs
                } else {
                    url += '?' + qs
                }
            }
            return url
        }

        function toJson(a) {
            //a可以为ArrayBuffer or Uint8Array
            return JSON.parse(new TextDecoder('utf-8').decode(a))
        }
        function toBlob(a, contentType) {
            //主要返回pdf或者zip?
            //当使用<embed> 显示Blob，必须指定{type:'application/pdf'}，否则<embed>
            //理解blob返回的字节为ascii字符串
            return new Blob([a], { type: contentType || 'application/pdf' })
        }
        function parseResult(a) {
            if (a.base64 === true) {
                //表示使用base64，简单如下解码
                return new Blob([Uint8Array.from(atob(a.data), (c) => c.charCodeAt(0))], { type: a.contentType || 'application/pdf' })
            } else {
                return a.data
            }
        }
        const query = new URLSearchParams()
        if (async) {
            query.set('async', 'true')
        }
        if (timeout !== null && timeout !== undefined) {
            query.set('timeout', timeout)
        }
        if (taskId !== null && taskId !== undefined && taskId !== '') {
            query.set('task_id', taskId)
        }

        //根据需要处理
        //xhr.responseType = 'json'
        //如果使用Blob，就需要使用异步转换为json
        xhr.responseType = 'arraybuffer'

        setListeners()

        if (useForm) {
            const form = new FormData()
            form.append('file', file)
            //其他参数使用json编码后，需要使用
            form.append('params', JSON.stringify(params))
            xhr.open('POST', makeURL(url, query))
            //可以设置header
            xhr.send(form)
        } else {
            //其他参数使用json编码后添加到url
            if (!utils.isEmpty(params)) {
                query.set('params', JSON.stringify(params))
            }
            xhr.open('POST', makeURL(url, query))
            xhr.send(file)
        }

    }

}


function demoJson() {
    function openJsonEditor() {
        const dialog = document.getElementById('jsoneditor-dialog')
        console.log(dialog)
        dialog.showModal()
    }
    function closeJsonEditor() {
        const dialog = document.getElementById('jsoneditor-dialog')
        //console.log(dialog)
        dialog.close()
    }
    document.getElementById('edit').addEventListener('click', openJsonEditor)
    //document.getElementById('close').addEventListener('click', closeJsonEditor)
    // create the editor
    const container = document.querySelector('#jsoneditor-dialog .x-jsoneditor')
    const options = {
        modes: ['text', 'code', 'tree', 'preview'],
        mode: 'code'
    }
    const editor = new JSONEditor(container, options)

    // set json
    const initialJson = {
        "Array": [1, 2, 3],
        "Boolean": true,
        "Null": null,
        "Number": 123,
        "Object": { "a": "b", "c": "d" },
        "String": "Hello World"
    }
    editor.set(initialJson)

    // get json
    const updatedJson = editor.get()
}


async function demo() {
    const res = await fetch('70.png.json')
    const result = await res.json()
    const html = new OCRPage().render(result, {})
    //console.log(html)
    document.getElementById('page-wrapper').innerHTML = html
}

window.onload = async () => {
    //await demo()
    const app = new App()
    await app.setup()

}
