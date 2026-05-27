const { createApp } = Vue;

createApp({
    data() {
        return {
            messages: [],
            userInput: '',
            isLoading: false,
            activeNav: 'newChat',
            abortController: null,
            sessionId: 'session_' + Date.now(),
            sessions: [],
            showHistorySidebar: false,
            isComposing: false,
            documents: [],
            documentsLoading: false,
            teacherArtifacts: [],
            artifactsLoading: false,
            artifactTypeFilter: '',
            teacherWorkspaceTab: 'questions',
            questionForm: {
                knowledge_topic: '',
                subject: '',
                grade: '',
                book_version: '',
                resource_type: '',
                question_type: 'short_answer',
                difficulty: 'medium',
                count: 3,
                save: false
            },
            lessonForm: {
                teaching_topic: '',
                subject: '',
                grade: '',
                book_version: '',
                resource_type: '',
                lesson_duration: 45,
                teaching_style: '',
                save: false
            },
            gradingForm: {
                question: '',
                student_answer: '',
                standard_answer: '',
                rubric: '',
                max_score: 10,
                knowledge_topic: '',
                subject: '',
                grade: '',
                book_version: '',
                resource_type: '',
                question_type: '',
                save: false
            },
            objectiveForm: {
                student_answer_text: '',
                answer_key_text: '',
                default_score: 1,
                save: false
            },
            objectiveImages: {
                student: null,
                answerKey: null,
                save: false
            },
            selectedFile: null,
            uploadMetadata: {
                subject: '',
                grade: '',
                book_version: '',
                resource_type: 'textbook',
                section_title: '',
                knowledge_tags: ''
            },
            isUploading: false,
            uploadProgress: '',
            uploadSteps: [],
            uploadProgressCollapsed: false,
            activeUploadJobId: '',
            uploadPollTimer: null,
            deleteJobs: {},
            deletePollTimers: {},
            deleteRemoveTimers: {},
            token: localStorage.getItem('accessToken') || '',
            currentUser: null,
            authMode: 'login',
            authForm: {
                username: '',
                password: ''
            },
            authLoading: false,
            sessionTokenUsage: null
        };
    },
    computed: {
        isAuthenticated() {
            return !!this.token && !!this.currentUser;
        },
        isAdmin() {
            return this.currentUser?.role === 'admin';
        }
    },
    async mounted() {
        this.configureMarked();
        if (this.token) {
            try {
                await this.fetchMe();
            } catch (_) {
                this.handleLogout();
            }
        }
    },
    beforeUnmount() {
        this.stopUploadJobPolling();
        this.stopAllDeleteJobPolling();
        Object.values(this.deleteRemoveTimers).forEach(timer => clearTimeout(timer));
    },
    methods: {
        configureMarked() {
            marked.setOptions({
                highlight: function(code, lang) {
                    const language = hljs.getLanguage(lang) ? lang : 'plaintext';
                    return hljs.highlight(code, { language }).value;
                },
                langPrefix: 'hljs language-',
                breaks: true,
                gfm: true
            });
        },

        parseMarkdown(text) {
            return marked.parse(text);
        },

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        },

        authHeaders(extra = {}) {
            const headers = { ...extra };
            if (this.token) {
                headers.Authorization = `Bearer ${this.token}`;
            }
            return headers;
        },

        async authFetch(url, options = {}) {
            const opts = { ...options };
            opts.headers = this.authHeaders(opts.headers || {});
            const response = await fetch(url, opts);
            if (response.status === 401) {
                this.handleLogout();
                throw new Error('登录已过期，请重新登录');
            }
            return response;
        },

        async fetchMe() {
            const response = await this.authFetch('/auth/me');
            if (!response.ok) {
                throw new Error('认证失败');
            }
            this.currentUser = await response.json();
        },

        async handleAuthSubmit() {
            if (this.authLoading) return;
            const username = this.authForm.username.trim();
            const password = this.authForm.password.trim();
            if (!username || !password) {
                alert('用户名和密码不能为空');
                return;
            }

            this.authLoading = true;
            try {
                const endpoint = this.authMode === 'login' ? '/auth/login' : '/auth/register';
                const payload = {
                    username,
                    password
                };

                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.detail || '认证失败');
                }

                this.token = data.access_token;
                this.currentUser = { username: data.username, role: data.role };
                localStorage.setItem('accessToken', this.token);
                this.authForm.password = '';
                this.messages = [];
                this.sessionTokenUsage = null;
                this.sessionId = 'session_' + Date.now();
                this.activeNav = 'newChat';
            } catch (error) {
                alert(error.message);
            } finally {
                this.authLoading = false;
            }
        },

        handleLogout() {
            this.token = '';
            this.currentUser = null;
            this.messages = [];
            this.sessions = [];
            this.documents = [];
            this.sessionTokenUsage = null;
            this.activeNav = 'newChat';
            this.showHistorySidebar = false;
            localStorage.removeItem('accessToken');
        },

        handleCompositionStart() {
            this.isComposing = true;
        },

        handleCompositionEnd() {
            this.isComposing = false;
        },

        handleKeyDown(event) {
            if (event.key === 'Enter' && !event.shiftKey && !this.isComposing) {
                event.preventDefault();
                this.handleSend();
            }
        },

        handleStop() {
            if (this.abortController) {
                this.abortController.abort();
            }
        },

        async handleSend() {
            if (!this.isAuthenticated) {
                alert('请先登录');
                return;
            }

            const text = this.userInput.trim();
            if (!text || this.isLoading || this.isComposing) return;

            this.messages.push({
                text: text,
                isUser: true
            });

            this.userInput = '';
            this.$nextTick(() => {
                this.resetTextareaHeight();
                this.scrollToBottom();
            });

            this.isLoading = true;
            this.messages.push({
                text: '',
                isUser: false,
                isThinking: true,
                ragTrace: null,
                ragSteps: []
            });
            const botMsgIdx = this.messages.length - 1;

            this.abortController = new AbortController();

            try {
                const response = await this.authFetch('/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: text,
                        session_id: this.sessionId
                    }),
                    signal: this.abortController.signal,
                });

                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                let buffer = '';
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });

                    let eventEndIndex;
                    while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
                        const eventStr = buffer.slice(0, eventEndIndex);
                        buffer = buffer.slice(eventEndIndex + 2);

                        if (eventStr.startsWith('data: ')) {
                            const dataStr = eventStr.slice(6);
                            if (dataStr === '[DONE]') continue;
                            try {
                                const data = JSON.parse(dataStr);
                                if (data.type === 'content') {
                                    if (this.messages[botMsgIdx].isThinking) {
                                        this.messages[botMsgIdx].isThinking = false;
                                    }
                                    this.messages[botMsgIdx].text += data.content;
                                } else if (data.type === 'agent_route') {
                                    this.messages[botMsgIdx].agentRoute = data.agent_route;
                                } else if (data.type === 'task_start') {
                                    if (!this.messages[botMsgIdx].ragSteps) {
                                        this.messages[botMsgIdx].ragSteps = [];
                                    }
                                    this.messages[botMsgIdx].ragSteps.push({
                                        icon: '▶',
                                        label: `任务 ${data.task_index}/${data.task_total}：${data.agent_route}`,
                                        detail: data.instruction
                                    });
                                } else if (data.type === 'trace') {
                                    this.messages[botMsgIdx].ragTrace = data.rag_trace;
                                } else if (data.type === 'token_usage') {
                                    this.sessionTokenUsage = data.token_usage;
                                } else if (data.type === 'rag_step') {
                                    if (!this.messages[botMsgIdx].ragSteps) {
                                        this.messages[botMsgIdx].ragSteps = [];
                                    }
                                    this.messages[botMsgIdx].ragSteps.push(data.step);
                                } else if (data.type === 'error') {
                                    this.messages[botMsgIdx].isThinking = false;
                                    this.messages[botMsgIdx].text += `\n[Error: ${data.content}]`;
                                }
                            } catch (e) {
                                console.warn('SSE parse error:', e);
                            }
                        }
                    }
                    this.$nextTick(() => this.scrollToBottom());
                }

            } catch (error) {
                if (error.name === 'AbortError') {
                    this.messages[botMsgIdx].isThinking = false;
                    if (!this.messages[botMsgIdx].text) {
                        this.messages[botMsgIdx].text = '(已终止回答)';
                    } else {
                        this.messages[botMsgIdx].text += '\n\n_(回答已被终止)_';
                    }
                } else {
                    this.messages[botMsgIdx].isThinking = false;
                    this.messages[botMsgIdx].text = `出了点问题：${error.message}`;
                }
            } finally {
                this.isLoading = false;
                this.abortController = null;
                await this.loadSessionTokenUsage();
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        autoResize(event) {
            const textarea = event.target;
            textarea.style.height = 'auto';
            textarea.style.height = textarea.scrollHeight + 'px';
        },

        resetTextareaHeight() {
            if (this.$refs.textarea) {
                this.$refs.textarea.style.height = 'auto';
            }
        },

        scrollToBottom() {
            if (this.$refs.chatContainer) {
                this.$refs.chatContainer.scrollTop = this.$refs.chatContainer.scrollHeight;
            }
        },

        handleNewChat() {
            if (!this.isAuthenticated) return;
            this.messages = [];
            this.sessionTokenUsage = null;
            this.sessionId = 'session_' + Date.now();
            this.activeNav = 'newChat';
            this.showHistorySidebar = false;
        },

        handleClearChat() {
            if (confirm('确定要清空当前对话吗？')) {
                this.messages = [];
                this.sessionTokenUsage = null;
            }
        },

        async loadSessionTokenUsage() {
            if (!this.isAuthenticated || !this.sessionId) return;
            try {
                const response = await this.authFetch(`/sessions/${encodeURIComponent(this.sessionId)}/token-usage`);
                if (!response.ok) return;
                this.sessionTokenUsage = await response.json();
            } catch (_) {
                this.sessionTokenUsage = null;
            }
        },

        async handleHistory() {
            if (!this.isAuthenticated) return;
            this.activeNav = 'history';
            this.showHistorySidebar = true;
            try {
                const response = await this.authFetch('/sessions');
                if (!response.ok) {
                    throw new Error('Failed to load sessions');
                }
                const data = await response.json();
                this.sessions = data.sessions;
            } catch (error) {
                alert('加载历史记录失败：' + error.message);
            }
        },

        async loadSession(sessionId) {
            this.sessionId = sessionId;
            this.showHistorySidebar = false;
            this.activeNav = 'newChat';

            try {
                const response = await this.authFetch(`/sessions/${encodeURIComponent(sessionId)}`);
                if (!response.ok) {
                    throw new Error('Failed to load session messages');
                }
                const data = await response.json();
                this.messages = data.messages.map(msg => ({
                    text: msg.content,
                    isUser: msg.type === 'human',
                    ragTrace: msg.rag_trace || null
                }));
                await this.loadSessionTokenUsage();

                this.$nextTick(() => {
                    this.scrollToBottom();
                });
            } catch (error) {
                alert('加载会话失败：' + error.message);
                this.messages = [];
                this.sessionTokenUsage = null;
            }
        },

        async deleteSession(sessionId) {
            if (!confirm(`确定要删除会话 "${sessionId}" 吗？`)) {
                return;
            }

            try {
                const response = await this.authFetch(`/sessions/${encodeURIComponent(sessionId)}`, {
                    method: 'DELETE'
                });

                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload.detail || 'Delete failed');
                }

                this.sessions = this.sessions.filter(s => s.session_id !== sessionId);

                if (this.sessionId === sessionId) {
                    this.messages = [];
                    this.sessionTokenUsage = null;
                    this.sessionId = 'session_' + Date.now();
                    this.activeNav = 'newChat';
                }

                if (payload.message) {
                    alert(payload.message);
                }
            } catch (error) {
                alert('删除会话失败：' + error.message);
            }
        },

        handleSettings() {
            if (!this.isAuthenticated) return;
            this.activeNav = 'settings';
            this.showHistorySidebar = false;
            this.loadDocuments();
        },

        handleTeacherWorkspace() {
            if (!this.isAuthenticated) return;
            this.activeNav = 'teacherWorkspace';
            this.showHistorySidebar = false;
        },

        handleArtifacts() {
            if (!this.isAuthenticated) return;
            this.activeNav = 'artifacts';
            this.showHistorySidebar = false;
            this.loadArtifacts();
        },

        appendTeacherTaskResult(taskLabel, payload) {
            this.activeNav = 'newChat';
            this.showHistorySidebar = false;
            const content = payload.content || payload.report || payload;
            const sourceText = (payload.source_chunk_ids || []).length
                ? `\n\n来源 chunk：${payload.source_chunk_ids.join(', ')}`
                : '';
            const verifierText = payload.verifier_notes ? `\n\n校验：${payload.verifier_notes}` : '';
            const routeText = payload.agent_route ? `\n\nAgent：${payload.agent_route}` : '';
            const savedText = payload.saved_artifact_id ? `\n\n已保存：#${payload.saved_artifact_id}` : '';
            this.messages.push({
                text: `${taskLabel}\n\n${JSON.stringify(content || {}, null, 2)}${sourceText}${verifierText}${routeText}${savedText}`,
                isUser: false,
                ragTrace: null,
                ragSteps: []
            });
            this.$nextTick(() => this.scrollToBottom());
        },

        async runTeacherTask(endpoint, payload, taskLabel) {
            if (this.isLoading) return;
            this.activeNav = 'newChat';
            this.showHistorySidebar = false;
            this.isLoading = true;
            this.messages.push({ text: `${taskLabel}请求\n\n${JSON.stringify(payload, null, 2)}`, isUser: true });
            const botMsgIdx = this.messages.length;
            this.messages.push({ text: '', isUser: false, isThinking: true, ragTrace: null, ragSteps: [] });
            try {
                const response = await this.authFetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.detail || `HTTP ${response.status}`);
                }
                this.messages.splice(botMsgIdx, 1);
                this.appendTeacherTaskResult(taskLabel, data);
            } catch (error) {
                this.messages[botMsgIdx].isThinking = false;
                this.messages[botMsgIdx].text = `${taskLabel}失败：${error.message}`;
            } finally {
                this.isLoading = false;
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        submitQuestionTask() {
            const topic = (this.questionForm.knowledge_topic || '').trim();
            if (!topic) {
                alert('请填写知识点');
                return;
            }
            this.runTeacherTask('/teacher/questions/generate', {
                ...this.questionForm,
                knowledge_topic: topic,
                count: Number(this.questionForm.count || 3)
            }, '智能出题');
        },

        submitLessonTask() {
            const topic = (this.lessonForm.teaching_topic || '').trim();
            if (!topic) {
                alert('请填写课题');
                return;
            }
            this.runTeacherTask('/teacher/lesson-plans/generate', {
                ...this.lessonForm,
                teaching_topic: topic,
                lesson_duration: Number(this.lessonForm.lesson_duration || 45)
            }, '备课设计');
        },

        submitGradingTask() {
            const question = (this.gradingForm.question || '').trim();
            const studentAnswer = (this.gradingForm.student_answer || '').trim();
            if (!question || !studentAnswer) {
                alert('请填写题目和学生答案');
                return;
            }
            this.runTeacherTask('/teacher/grading/generate', {
                ...this.gradingForm,
                question,
                student_answer: studentAnswer,
                max_score: Number(this.gradingForm.max_score || 10)
            }, '批改参考');
        },

        submitObjectiveGradingTask() {
            const studentText = (this.objectiveForm.student_answer_text || '').trim();
            const answerKeyText = (this.objectiveForm.answer_key_text || '').trim();
            if (!studentText || !answerKeyText) {
                alert('请填写学生答案和标准答案');
                return;
            }
            this.runTeacherTask('/teacher/objective-grading/generate', {
                ...this.objectiveForm,
                student_answer_text: studentText,
                answer_key_text: answerKeyText,
                default_score: Number(this.objectiveForm.default_score || 1)
            }, '客观题批改');
        },

        handleObjectiveStudentImage(event) {
            this.objectiveImages.student = event.target.files?.[0] || null;
        },

        handleObjectiveAnswerKeyImage(event) {
            this.objectiveImages.answerKey = event.target.files?.[0] || null;
        },

        async submitObjectiveImageGradingTask() {
            if (!this.objectiveImages.student || !this.objectiveImages.answerKey) {
                alert('请选择学生答案图片和标准答案图片');
                return;
            }
            if (this.isLoading) return;
            this.activeNav = 'newChat';
            this.showHistorySidebar = false;
            this.isLoading = true;
            const formData = new FormData();
            formData.append('student_image', this.objectiveImages.student);
            formData.append('answer_key_image', this.objectiveImages.answerKey);
            formData.append('save', this.objectiveImages.save ? 'true' : 'false');
            this.messages.push({
                text: `客观题图片批改请求\n\n学生答案：${this.objectiveImages.student.name}\n标准答案：${this.objectiveImages.answerKey.name}`,
                isUser: true
            });
            const botMsgIdx = this.messages.length;
            this.messages.push({ text: '', isUser: false, isThinking: true, ragTrace: null, ragSteps: [] });
            try {
                const response = await this.authFetch('/teacher/objective-grading/images', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.detail || `HTTP ${response.status}`);
                }
                this.messages.splice(botMsgIdx, 1);
                this.appendTeacherTaskResult('客观题图片批改', data);
            } catch (error) {
                this.messages[botMsgIdx].isThinking = false;
                this.messages[botMsgIdx].text = `客观题图片批改失败：${error.message}`;
            } finally {
                this.isLoading = false;
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        async loadArtifacts() {
            this.artifactsLoading = true;
            try {
                const params = this.artifactTypeFilter ? `?artifact_type=${encodeURIComponent(this.artifactTypeFilter)}` : '';
                const response = await this.authFetch(`/teacher/artifacts${params}`);
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Failed to load artifacts');
                }
                const data = await response.json();
                this.teacherArtifacts = data.artifacts || [];
            } catch (error) {
                alert('加载生成记录失败：' + error.message);
            } finally {
                this.artifactsLoading = false;
            }
        },

        async deleteArtifact(id) {
            if (!confirm('确定要删除这条生成记录吗？')) return;
            try {
                const response = await this.authFetch(`/teacher/artifacts/${id}`, { method: 'DELETE' });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Delete failed');
                }
                this.teacherArtifacts = this.teacherArtifacts.filter(item => item.id !== id);
            } catch (error) {
                alert('删除生成记录失败：' + error.message);
            }
        },

        formatArtifactType(type) {
            const labels = {
                question_set: '题目',
                lesson_plan: '教案',
                homework: '作业',
                grading_reference: '批改参考',
                objective_grading_report: '客观题批改报告',
                chapter_summary: '章节总结'
            };
            return labels[type] || type;
        },

        formatArtifactPreview(content) {
            if (!content || Object.keys(content).length === 0) {
                return '暂无内容';
            }
            return JSON.stringify(content, null, 2);
        },

        async saveMessageAsArtifact(index) {
            const message = this.messages[index];
            if (!message || !message.text) return;

            const title = prompt('生成记录标题', message.text.slice(0, 30).replace(/\s+/g, ' ') || 'AI 生成材料');
            if (!title) return;
            const artifactType = prompt('材料类型：question_set / lesson_plan / homework / grading_reference / chapter_summary', 'chapter_summary');
            if (!artifactType) return;

            const previousUserMessage = [...this.messages.slice(0, index)].reverse().find(item => item.isUser);
            const chunks = [
                ...(message.ragTrace?.retrieved_chunks || []),
                ...(message.ragTrace?.expanded_retrieved_chunks || []),
                ...(message.ragTrace?.initial_retrieved_chunks || [])
            ];
            const sourceChunkIds = [...new Set(chunks.map(chunk => chunk.chunk_id).filter(Boolean))];

            try {
                const response = await this.authFetch('/teacher/artifacts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        artifact_type: artifactType.trim(),
                        title: title.trim(),
                        prompt: previousUserMessage?.text || '',
                        content_json: {
                            content: message.text,
                            rag_trace: message.ragTrace || null
                        },
                        source_chunk_ids: sourceChunkIds
                    })
                });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Save failed');
                }
                alert('已保存到生成记录');
            } catch (error) {
                alert('保存生成记录失败：' + error.message);
            }
        },

        mergeDocumentsWithActiveDeletes(nextDocuments) {
            const merged = Array.isArray(nextDocuments) ? [...nextDocuments] : [];
            Object.keys(this.deleteJobs).forEach(filename => {
                const job = this.deleteJobs[filename];
                if (!job || job.status === 'failed') return;
                const exists = merged.some(doc => doc.filename === filename);
                if (!exists) {
                    const currentDoc = this.documents.find(doc => doc.filename === filename);
                    if (currentDoc) {
                        merged.push(currentDoc);
                    }
                }
            });
            return merged;
        },

        async loadDocuments() {
            this.documentsLoading = true;
            try {
                const response = await this.authFetch('/documents');
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Failed to load documents');
                }
                const data = await response.json();
                this.documents = this.mergeDocumentsWithActiveDeletes(data.documents);
            } catch (error) {
                alert('加载文档列表失败：' + error.message);
            } finally {
                this.documentsLoading = false;
            }
        },

        handleFileSelect(event) {
            const files = event.target.files;
            if (files && files.length > 0) {
                this.selectedFile = files[0];
                this.uploadProgress = '';
                this.uploadSteps = this.createUploadSteps();
                this.uploadProgressCollapsed = false;
                this.activeUploadJobId = '';
            }
        },

        createUploadSteps() {
            return [
                { key: 'upload', label: '文档上传', percent: 0, status: 'pending', message: '' },
                { key: 'cleanup', label: '清理旧版本', percent: 0, status: 'pending', message: '' },
                { key: 'parse', label: '解析与分块', percent: 0, status: 'pending', message: '' },
                { key: 'parent_store', label: '父级分块入库', percent: 0, status: 'pending', message: '' },
                { key: 'vector_store', label: '向量化入库', percent: 0, status: 'pending', message: '' },
                { key: 'graph_store', label: '知识图谱同步', percent: 0, status: 'pending', message: '' },
            ];
        },

        updateUploadStep(key, percent, status = 'running', message = '') {
            if (!this.uploadSteps.length) {
                this.uploadSteps = this.createUploadSteps();
            }
            const idx = this.uploadSteps.findIndex(step => step.key === key);
            if (idx === -1) return;
            this.uploadSteps[idx] = {
                ...this.uploadSteps[idx],
                percent: Math.max(0, Math.min(100, Math.round(percent || 0))),
                status,
                message
            };
        },

        uploadFileWithProgress(file) {
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                const formData = new FormData();
                formData.append('file', file);
                formData.append('subject', this.uploadMetadata.subject || '');
                formData.append('grade', this.uploadMetadata.grade || '');
                formData.append('book_version', this.uploadMetadata.book_version || '');
                formData.append('resource_type', this.uploadMetadata.resource_type || 'textbook');
                formData.append('section_title', this.uploadMetadata.section_title || '');
                formData.append('knowledge_tags', this.uploadMetadata.knowledge_tags || '');

                xhr.open('POST', '/documents/upload/async');
                const headers = this.authHeaders();
                Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));

                xhr.upload.onprogress = (event) => {
                    if (!event.lengthComputable) return;
                    const percent = Math.round((event.loaded / event.total) * 100);
                    this.updateUploadStep('upload', percent, 'running', `已上传 ${percent}%`);
                };

                xhr.onload = () => {
                    if (xhr.status === 401) {
                        this.handleLogout();
                        reject(new Error('登录已过期，请重新登录'));
                        return;
                    }

                    let data = {};
                    try {
                        data = JSON.parse(xhr.responseText || '{}');
                    } catch (e) {
                        reject(new Error('上传响应解析失败'));
                        return;
                    }

                    if (xhr.status < 200 || xhr.status >= 300) {
                        reject(new Error(data.detail || `HTTP ${xhr.status}`));
                        return;
                    }

                    this.updateUploadStep('upload', 100, 'completed', '文档上传完成');
                    resolve(data);
                };

                xhr.onerror = () => reject(new Error('上传请求失败'));
                xhr.onabort = () => reject(new Error('上传已取消'));
                xhr.send(formData);
            });
        },

        syncUploadJob(job) {
            this.activeUploadJobId = job.job_id;
            this.uploadProgress = job.message || '';
            if (Array.isArray(job.steps)) {
                this.uploadSteps = job.steps.map(step => ({
                    key: step.key,
                    label: step.label,
                    percent: step.percent,
                    status: step.status,
                    message: step.message || ''
                }));
            }
            // 入库成功后自动收起步骤明细，保留摘要供用户再次展开查看。
            if (job.status === 'completed') {
                this.uploadProgressCollapsed = true;
            }
        },

        toggleUploadProgressCollapsed() {
            this.uploadProgressCollapsed = !this.uploadProgressCollapsed;
        },

        stopUploadJobPolling() {
            if (this.uploadPollTimer) {
                clearInterval(this.uploadPollTimer);
                this.uploadPollTimer = null;
            }
        },

        startUploadJobPolling(jobId) {
            this.stopUploadJobPolling();

            const poll = async () => {
                try {
                    const response = await this.authFetch(`/documents/upload/jobs/${encodeURIComponent(jobId)}`);
                    if (!response.ok) {
                        const error = await response.json().catch(() => ({}));
                        throw new Error(error.detail || 'Failed to load upload job');
                    }

                    const job = await response.json();
                    this.syncUploadJob(job);

                    if (job.status === 'completed') {
                        this.stopUploadJobPolling();
                        this.isUploading = false;
                        this.selectedFile = null;
                        if (this.$refs.fileInput) {
                            this.$refs.fileInput.value = '';
                        }
                        await this.loadDocuments();
                    } else if (job.status === 'failed') {
                        this.stopUploadJobPolling();
                        this.isUploading = false;
                    }
                } catch (error) {
                    this.uploadProgress = '进度查询失败：' + error.message;
                    this.stopUploadJobPolling();
                    this.isUploading = false;
                }
            };

            poll();
            this.uploadPollTimer = setInterval(poll, 1000);
        },

        async uploadDocument() {
            if (!this.selectedFile) {
                alert('请先选择文件');
                return;
            }

            this.isUploading = true;
            this.uploadProgress = '正在上传...';
            this.uploadSteps = this.createUploadSteps();
            this.uploadProgressCollapsed = false;
            this.updateUploadStep('upload', 0, 'running', '准备上传');

            try {
                const data = await this.uploadFileWithProgress(this.selectedFile);
                this.uploadProgress = data.message;
                this.activeUploadJobId = data.job_id;
                this.startUploadJobPolling(data.job_id);
            } catch (error) {
                this.updateUploadStep('upload', 100, 'failed', error.message);
                this.uploadProgress = '上传失败：' + error.message;
                this.isUploading = false;
            }
        },

        createDeleteSteps() {
            return [
                { key: 'prepare', label: '准备删除', percent: 0, status: 'pending', message: '' },
                { key: 'bm25', label: '同步 BM25 统计', percent: 0, status: 'pending', message: '' },
                { key: 'milvus', label: '删除向量数据', percent: 0, status: 'pending', message: '' },
                { key: 'parent_store', label: '删除父级分块', percent: 0, status: 'pending', message: '' },
                { key: 'graph_store', label: '删除图谱引用', percent: 0, status: 'pending', message: '' },
            ];
        },

        isDeletingDocument(filename) {
            const job = this.deleteJobs[filename];
            return job && job.status === 'running';
        },

        isDeleteActionLocked(filename) {
            const job = this.deleteJobs[filename];
            return job && (job.status === 'running' || job.status === 'completed');
        },

        getDeleteButtonIcon(filename) {
            const job = this.deleteJobs[filename];
            if (job?.status === 'running') return 'fas fa-spinner fa-spin';
            if (job?.status === 'completed') return 'fas fa-check';
            return 'fas fa-trash';
        },

        setDeleteJob(filename, nextJob) {
            this.deleteJobs = {
                ...this.deleteJobs,
                [filename]: {
                    ...(this.deleteJobs[filename] || {}),
                    ...nextJob
                }
            };
        },

        syncDeleteJob(filename, job) {
            const current = this.deleteJobs[filename] || {};
            // 后端返回统一的步骤结构，前端只负责同步到当前文档行内卡片。
            this.setDeleteJob(filename, {
                jobId: job.job_id,
                status: job.status,
                message: job.message || '',
                collapsed: job.status === 'completed' ? true : Boolean(current.collapsed),
                steps: Array.isArray(job.steps) ? job.steps.map(step => ({
                    key: step.key,
                    label: step.label,
                    percent: step.percent,
                    status: step.status,
                    message: step.message || ''
                })) : this.createDeleteSteps()
            });
        },

        toggleDeleteJobCollapsed(filename) {
            const job = this.deleteJobs[filename];
            if (!job) return;
            this.setDeleteJob(filename, { collapsed: !job.collapsed });
        },

        stopDeleteJobPolling(filename) {
            const timer = this.deletePollTimers[filename];
            if (!timer) return;
            clearInterval(timer);
            const { [filename]: _removed, ...rest } = this.deletePollTimers;
            this.deletePollTimers = rest;
        },

        stopAllDeleteJobPolling() {
            Object.keys(this.deletePollTimers).forEach(filename => this.stopDeleteJobPolling(filename));
        },

        clearDeleteRemovalTimer(filename) {
            const timer = this.deleteRemoveTimers[filename];
            if (!timer) return;
            clearTimeout(timer);
            const { [filename]: _removed, ...rest } = this.deleteRemoveTimers;
            this.deleteRemoveTimers = rest;
        },

        scheduleDeletedDocumentRemoval(filename) {
            this.clearDeleteRemovalTimer(filename);
            // 删除完成后先保留 3 秒摘要，再从当前列表移除并刷新后端状态。
            const timer = setTimeout(async () => {
                this.documents = this.documents.filter(doc => doc.filename !== filename);
                const { [filename]: _job, ...jobs } = this.deleteJobs;
                const { [filename]: _timer, ...timers } = this.deleteRemoveTimers;
                this.deleteJobs = jobs;
                this.deleteRemoveTimers = timers;
                await this.loadDocuments();
            }, 3000);
            this.deleteRemoveTimers = {
                ...this.deleteRemoveTimers,
                [filename]: timer
            };
        },

        startDeleteJobPolling(filename, jobId) {
            this.stopDeleteJobPolling(filename);

            const poll = async () => {
                try {
                    const response = await this.authFetch(`/documents/delete/jobs/${encodeURIComponent(jobId)}`);
                    if (!response.ok) {
                        const error = await response.json().catch(() => ({}));
                        throw new Error(error.detail || 'Failed to load delete job');
                    }

                    const job = await response.json();
                    this.syncDeleteJob(filename, job);

                    if (job.status === 'completed') {
                        this.stopDeleteJobPolling(filename);
                        this.scheduleDeletedDocumentRemoval(filename);
                    } else if (job.status === 'failed') {
                        this.stopDeleteJobPolling(filename);
                    }
                } catch (error) {
                    this.setDeleteJob(filename, {
                        status: 'failed',
                        message: '删除进度查询失败：' + error.message,
                        collapsed: false,
                        steps: this.deleteJobs[filename]?.steps || this.createDeleteSteps()
                    });
                    this.stopDeleteJobPolling(filename);
                }
            };

            poll();
            this.deletePollTimers = {
                ...this.deletePollTimers,
                [filename]: setInterval(poll, 1000)
            };
        },

        async deleteDocument(filename) {
            if (this.isDeletingDocument(filename)) {
                return;
            }
            if (!confirm(`确定要删除文档 "${filename}" 吗？这将同时删除 Milvus 中的所有相关向量。`)) {
                return;
            }

            this.clearDeleteRemovalTimer(filename);
            this.setDeleteJob(filename, {
                status: 'running',
                message: '正在提交删除任务...',
                collapsed: false,
                steps: this.createDeleteSteps().map(step => (
                    step.key === 'prepare'
                        ? { ...step, percent: 1, status: 'running', message: '正在提交删除任务' }
                        : step
                ))
            });

            try {
                const response = await this.authFetch(`/documents/delete/async/${encodeURIComponent(filename)}`, {
                    method: 'DELETE'
                });

                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(error.detail || 'Delete failed');
                }

                const data = await response.json();
                this.setDeleteJob(filename, {
                    jobId: data.job_id,
                    status: 'running',
                    message: data.message || `正在删除 ${filename}`,
                    collapsed: false
                });
                this.startDeleteJobPolling(filename, data.job_id);

            } catch (error) {
                this.setDeleteJob(filename, {
                    status: 'failed',
                    message: '删除文档失败：' + error.message,
                    collapsed: false,
                    steps: this.deleteJobs[filename]?.steps || this.createDeleteSteps()
                });
            }
        },

        getFileIcon(fileType) {
            if (fileType === 'PDF') {
                return 'fas fa-file-pdf';
            } else if (fileType === 'Word') {
                return 'fas fa-file-word';
            } else if (fileType === 'Excel') {
                return 'fas fa-file-excel';
            }
            return 'fas fa-file';
        }
    },
    watch: {
        messages: {
            handler() {
                this.$nextTick(() => {
                    this.scrollToBottom();
                });
            },
            deep: true
        }
    }
}).mount('#app');
