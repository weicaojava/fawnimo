import { FC } from 'react'

interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
}

/** Share button states and hit areas without imposing business actions on its callers. */
const CustomButton: FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  children,
  onClick,
  disabled = false,
}) => {
  const baseStyles = 'studio-custom-button font-medium rounded-lg'

  const variants = {
    primary: 'studio-button-primary',
    secondary: 'studio-button-secondary',
    danger: 'studio-button-danger',
  }

  const sizes = {
    sm: 'px-3 py-1 text-sm',
    md: 'px-4 py-2 text-base',
    lg: 'px-6 py-3 text-lg',
  }

  return (
    <button
      type="button"
      className={`${baseStyles} ${variants[variant]} ${sizes[size]}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  )
}

export default CustomButton
