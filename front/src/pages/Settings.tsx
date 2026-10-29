import { useEffect, useState } from 'react'
import { Form, Input, Select, Switch, Button, Radio, message } from 'antd'
import { BulbOutlined, DesktopOutlined } from '@ant-design/icons'
import { useAppStore } from '../store/useAppStore'
import { useTranslation } from 'react-i18next'
import { useThemeStore } from '../theme/useThemeStore'
import { useIdentity } from '../identity/IdentityGate'
import { IdentityService } from '../services/generated'
import { WorkspaceBudgetPanel } from '../identity/WorkspaceBudgetPanel'
import './aiStudio/assets/components/LibraryPages.css'

/** Apply the shared appearance preference immediately while preserving profile form saving. */
const Settings: React.FC = () => {
  const identity = useIdentity()
  const trustedRole = identity ? ({ owner: '所有者', editor: '编辑者', viewer: '查看者' }[identity.role]) : null
  const [saving, setSaving] = useState(false)
  const { t, i18n } = useTranslation(['settings', 'common'])
  const user = useAppStore((state) => state.user)
  const setUser = useAppStore((state) => state.setUser)
  const mode = useThemeStore((state) => state.mode)
  const setMode = useThemeStore((state) => state.setMode)
  const isEnglish = i18n.resolvedLanguage?.startsWith('en') ?? false

  const [form] = Form.useForm()

  useEffect(() => {
    form.setFieldValue('darkMode', mode === 'dark')
  }, [form, mode])

  /** Save the existing nickname and role without resetting the chosen appearance. */
  const handleFinish = async (values: { name: string; role: string; darkMode: boolean }) => {
    setSaving(true)
    try {
      if (identity) {
        const saved = await IdentityService.profileApiV1IdentityMePatch({ requestBody: { display_name: values.name } })
        if (!saved.data) throw new Error('个人资料保存失败')
        setUser({ name: saved.data.user.display_name })
      } else setUser({ name: values.name, role: values.role })
      message.success(t('updated'))
    } catch { message.error('个人资料保存失败，请刷新后重试') }
    finally { setSaving(false) }
  }

  return (
    <div className="library-page">
      <header className="library-page-header">
        <h1 className="library-page-title">{t('title')}</h1>
      </header>
      <Form
        form={form}
        layout="vertical"
        className="library-panel library-settings-panel"
        initialValues={{
          name: user.name,
          role: trustedRole ?? user.role,
          darkMode: mode === 'dark',
        }}
        onFinish={handleFinish}
      >
        <section className="library-settings-section">
          <h2 className="library-section-title">{isEnglish ? 'Profile' : '基础信息'}</h2>
          <div className="library-settings-fields">
            <Form.Item
              label={t('nickname')}
              name="name"
              rules={[{ required: true, message: t('validation.nicknameRequired') }]}
            >
              <Input placeholder={t('nickname')} autoComplete="nickname" />
            </Form.Item>
            <Form.Item
              label={t('role')}
              name="role"
              rules={[{ required: true, message: t('validation.roleRequired') }]}
            >
              <Select disabled={!!identity}
                options={[
                  ...(trustedRole ? [{label: trustedRole, value: trustedRole}] : []),
                  { label: t('roleOptions.admin'), value: t('roleOptions.admin') },
                  { label: t('roleOptions.operator'), value: t('roleOptions.operator') },
                  { label: t('roleOptions.guest'), value: t('roleOptions.guest') },
                ]}
              />
            </Form.Item>
          </div>
        </section>

        <section className="library-settings-section">
          <h2 className="library-section-title">{isEnglish ? 'Appearance' : '界面外观'}</h2>
          <div className="library-settings-fields">
            <Radio.Group
              className="library-appearance-choices"
              aria-label={isEnglish ? 'Appearance' : '界面外观'}
              value={mode}
              onChange={(event) => setMode(event.target.value as 'light' | 'dark')}
            >
              <Radio.Button value="light">
                <span className="library-appearance-label"><BulbOutlined />{isEnglish ? 'Light' : '浅色模式'}</span>
              </Radio.Button>
              <Radio.Button value="dark">
                <span className="library-appearance-label"><DesktopOutlined />{isEnglish ? 'Dark' : '深色模式'}</span>
              </Radio.Button>
            </Radio.Group>
            <Form.Item
              hidden
              label={t('darkMode')}
              name="darkMode"
              valuePropName="checked"
              tooltip={isEnglish ? 'Applies immediately and is remembered on this device.' : '即时应用，并在此设备上保留你的选择。'}
            >
              <Switch aria-label={t('darkMode')} onChange={(checked) => setMode(checked ? 'dark' : 'light')} />
            </Form.Item>
          </div>
        </section>

        <Form.Item className="library-settings-actions">
          <Button type="primary" htmlType="submit" loading={saving}>
            {t('common:save')}
          </Button>
        </Form.Item>
      </Form>
      {identity && <WorkspaceBudgetPanel />}
    </div>
  )
}

export default Settings
