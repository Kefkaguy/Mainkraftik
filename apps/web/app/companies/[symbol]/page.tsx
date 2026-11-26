import { Workspace } from '@/components/Workspace';
export default async function Page({params}:{params:Promise<{symbol:string}>}) {
  const {symbol} = await params;
  return <Workspace initialSymbol={symbol} />;
}
