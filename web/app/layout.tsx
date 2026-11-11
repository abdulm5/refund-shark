import type { Metadata } from 'next';
import '@fontsource-variable/inter';
import './globals.css';
import './recovery.css';

export const metadata: Metadata = { title: 'Refund Shark — Service credits', description: 'Find the service credits hiding in your vendor agreements. An independent, local demo.' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
