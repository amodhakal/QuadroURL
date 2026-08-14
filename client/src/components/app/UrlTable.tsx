import { format } from "date-fns"
import { Copy, ExternalLink, MoreHorizontal, Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { shortLink } from "@/api/urls"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useDeleteUrl, useToggleUrlActive, useUrls } from "@/hooks/use-urls"
import { useUsers } from "@/hooks/use-users"
import { copyText } from "@/lib/api"
import type { Url } from "@/types/api"

function OwnerCell({ userId }: { userId: number }) {
  const { data: users } = useUsers()
  const user = users?.sample.find((u) => u.id === userId)
  return (
    <span className="text-muted-foreground">
      {user?.username ?? `#${userId}`}
    </span>
  )
}

export function UrlTable() {
  const { data, isLoading, isError } = useUrls()
  const toggleActive = useToggleUrlActive()
  const deleteUrl = useDeleteUrl()
  const [deleting, setDeleting] = useState<Url | null>(null)

  function onCopy(url: Url) {
    copyText(shortLink(url.short_code))
    toast.success("Link copied")
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your links</CardTitle>
        <CardDescription>Recently shortened URLs across all owners.</CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? (
          <div className="space-y-3 p-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : isError ? (
          <p className="p-6 text-sm text-destructive">
            Failed to load links. Is the API running?
          </p>
        ) : data?.sample.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">
            No links yet. Shorten your first URL above.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Short link</TableHead>
                <TableHead>Original</TableHead>
                <TableHead>Owner</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Active</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.sample.map((url) => (
                <TableRow key={url.id}>
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      <span className="font-mono text-sm font-medium">
                        /{url.short_code}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {url.title}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="max-w-[240px]">
                    <a
                      href={url.original_url}
                      target="_blank"
                      rel="noreferrer"
                      className="block truncate text-sm text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {url.original_url}
                    </a>
                  </TableCell>
                  <TableCell>
                    <OwnerCell userId={url.user_id} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {format(new Date(url.created_at), "MMM d, yyyy")}
                  </TableCell>
                  <TableCell className="text-right">
                    <Switch
                      checked={url.is_active}
                      disabled={toggleActive.isPending}
                      onCheckedChange={(checked) =>
                        toggleActive.mutate({ id: url.id, is_active: checked })
                      }
                      aria-label={`Toggle ${url.short_code}`}
                    />
                  </TableCell>
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon-sm" aria-label="Actions">
                          <MoreHorizontal />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => onCopy(url)}>
                          <Copy /> Copy link
                        </DropdownMenuItem>
                        <DropdownMenuItem asChild>
                          <a
                            href={shortLink(url.short_code)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <ExternalLink /> Open
                          </a>
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="text-destructive"
                          onClick={() => setDeleting(url)}
                        >
                          <Trash2 /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <AlertDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this short link?</AlertDialogTitle>
            <AlertDialogDescription>
              <span className="font-mono">/{deleting?.short_code}</span> will be
              permanently removed. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={deleteUrl.isPending}
              onClick={() => {
                if (deleting) deleteUrl.mutate(deleting.id)
                setDeleting(null)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}
