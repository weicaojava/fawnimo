import { FC } from 'react'

interface CardProps {
  title?: string
  description?: string
  icon?: React.ReactNode
  children?: React.ReactNode
}

/** Present optional supporting content using the same surfaces as application panels. */
const CustomCard: FC<CardProps> = ({ title, description, icon, children }) => {
  return (
    <div className="studio-support-panel p-6">
      {icon && <div className="mb-4 text-3xl">{icon}</div>}
      {title && <h3 className="text-lg font-semibold text-gray-800 mb-2">{title}</h3>}
      {description && <p className="text-gray-600 text-sm mb-4">{description}</p>}
      {children && <div>{children}</div>}
    </div>
  )
}

export default CustomCard
