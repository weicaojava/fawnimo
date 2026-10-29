import { Button, Result } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import './aiStudio/assets/components/LibraryPages.css'

/** Keep the existing return-home action clear in both appearance modes. */
const NotFound: React.FC = () => {
  const navigate = useNavigate()
  const { t } = useTranslation('notFound')

  return (
    <div className="library-page library-not-found">
      <Result
      status="404"
      title="404"
      subTitle={t('subTitle')}
      extra={
        <Button type="primary" onClick={() => navigate('/')}>
          {t('backHome')}
        </Button>
      }
      />
    </div>
  )
}

export default NotFound
