import * as React from "react"
import { Switch } from "radix-ui"

import { cn } from "@/lib/utils"

function SwitchThumb({
  className,
  ...props
}: React.ComponentProps<typeof Switch.Thumb>) {
  return (
    <Switch.Thumb
      data-slot="switch-thumb"
      className={cn(
        "pointer-events-none block size-4 shrink-0 rounded-full bg-background shadow-sm ring-0 transition-transform data-[state=checked]:translate-x-[18px] data-[state=unchecked]:translate-x-0.5",
        className,
      )}
      {...props}
    />
  )
}

function SwitchRoot({
  className,
  ...props
}: React.ComponentProps<typeof Switch.Root>) {
  return (
    <Switch.Root
      data-slot="switch"
      className={cn(
        "peer inline-flex h-6 w-10 shrink-0 items-center rounded-full border border-transparent transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
        className,
      )}
      {...props}
    >
      <SwitchThumb />
    </Switch.Root>
  )
}

export { SwitchRoot as Switch, SwitchThumb }
