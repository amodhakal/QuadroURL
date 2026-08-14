import { useState } from "react"

import { UserDialog } from "@/components/app/UserDialog"
import { UserTable } from "@/components/app/UserTable"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { UserPlus } from "lucide-react"

export function Users() {
  const [dialogOpen, setDialogOpen] = useState(false)

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-4 py-10 sm:px-6">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
          <p className="text-sm text-muted-foreground">
            Manage the owners of short links.
          </p>
        </div>
        <Button onClick={() => setDialogOpen(true)}>
          <UserPlus /> New user
        </Button>
      </div>
      <Card>
        <CardContent className="p-0">
          <UserTable />
        </CardContent>
      </Card>
      <UserDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  )
}
